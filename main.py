"""Deterministic release-day Twitch forecasts using launch analogs."""

import datetime as dt
import json
import math
import os
import statistics


LEVEL_Z = (-1.6448536269514722, -0.6744897501960817, 0.0,
           0.6744897501960817, 1.6448536269514722)
LOG_MAX = math.log(1_000_000_000)


def number(value, default=1.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError, OverflowError):
        return default


def count(value):
    return max(1.0, min(1_000_000_000.0, number(value)))


def rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def instant(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def hourly_grid(records, start):
    """Group duplicate hourly observations deterministically in log space."""
    buckets = {}
    if start is None:
        return buckets
    for row in records:
        stamp = instant(row.get("from"))
        if stamp is None:
            continue
        offset = (stamp - start) / 3600.0
        hour = round(offset)
        # Only genuine hourly bins, allowing a minute of timestamp rounding.
        if abs(offset - hour) > 1 / 60 or not -336 <= hour <= 71:
            continue
        buckets.setdefault(hour, []).append(math.log(count(row.get("viewers"))))
    return {hour: statistics.median(sorted(buckets[hour])) for hour in sorted(buckets)}


def window(grid, first, last):
    values = [grid[hour] for hour in range(first, last) if hour in grid]
    return statistics.median(sorted(values)) if len(values) >= 2 else None


def features(grid, anchor):
    previous = window(grid, -24, 0)
    recent = window(grid, -6, 0)
    earlier = window(grid, -12, -6)
    final = window(grid, -3, 0)
    morning = window(grid, -24, -12)
    return (None if previous is None else anchor - previous,
            None if recent is None or earlier is None else recent - earlier,
            None if final is None else anchor - final,
            None if recent is None or morning is None else recent - morning)


def growth_signal(values):
    # The final ramp gets most influence; a full-day rise provides context.
    # Tanh bounds noisy early prints without discarding any usable signal.
    coefficients = (0.24, 0.19, 0.39, 0.18)
    available = [(values[index], coefficients[index]) for index in range(4)
                 if values[index] is not None]
    if not available:
        return None
    return math.fsum(weight * math.tanh(value / 1.3) for value, weight in available) / math.fsum(
        weight for _, weight in available)


def weighted_quantile(samples, probability):
    ordered = sorted(samples, key=lambda item: (item[0], item[1]))
    total = math.fsum(weight for _, weight in ordered)
    # Interpolate between centers of weighted probability masses, so equal
    # weights give the usual midpoint median for an even number of analogs.
    cumulative = 0.0
    previous_position = None
    previous_value = ordered[0][0]
    for value, weight in ordered:
        position = (cumulative + weight / 2) / total
        if probability <= position:
            if previous_position is None:
                return value
            fraction = (probability - previous_position) / (position - previous_position)
            return previous_value + fraction * (value - previous_value)
        cumulative += weight
        previous_position, previous_value = position, value
    return ordered[-1][0]


def fallback_shape(hour):
    # With no usable analogs, staggered US access ramps through 08 UTC;
    # a broad 17 UTC audience peak then fades toward the end of the day.
    # These are qualitative prior choices, not fitted launch measurements.
    def curve(t):
        unlock = 1.0 + 0.65 * min(1.0, max(0.0, (t - 1) / 7))
        daytime = 1.0 + 0.65 * math.exp(-0.5 * ((t - 17) / 5.5) ** 2)
        return unlock * daytime
    return math.log(curve(hour) / curve(1))


def forecast(case):
    if not isinstance(case, dict):
        case = {}
    known = sorted(rows(case.get("known")),
                   key=lambda row: (number(row.get("hour")), str(row.get("from", ""))))
    first = next((row for row in known if number(row.get("hour")) == 1), {})
    start = instant(case.get("day_start"))
    if start is None:
        start = instant(first.get("from"))
    target_grid = hourly_grid(rows(case.get("history_hourly")), start)
    # Raw observations supply missing hourly means, with only pre-checkpoint
    # observations eligible. Hourly summaries remain the preferred signal.
    raw = {}
    if start is not None:
        for row in rows(case.get("history")):
            stamp = instant(row.get("at"))
            if stamp is not None and start - 336 * 3600 <= stamp < start + 3600:
                hour = math.floor((stamp - start) / 3600)
                raw.setdefault(hour, []).append(count(row.get("viewers")))
    for hour in sorted(raw):
        if hour not in target_grid:
            target_grid[hour] = math.log(math.fsum(sorted(raw[hour])) / len(raw[hour]))
    anchor = math.log(count(first.get("viewers"))) if first else target_grid.get(0, 0.0)
    target_features = features(target_grid, anchor)
    target_signal = growth_signal(target_features)
    analogs = []
    comparable_rows = sorted(rows(case.get("comparables")),
                             key=lambda row: (str(row.get("name", "")),
                                              str(row.get("day_start", ""))))
    for comparable in comparable_rows:
        grid = hourly_grid(rows(comparable.get("hourly")), instant(comparable.get("day_start")))
        if 0 not in grid:
            continue
        origin = grid[0]
        # Near-zero anchors create arbitrary ratios; discount them smoothly.
        reliability = min(1.0, math.exp(origin) / 100.0)
        other_features = features(grid, origin)
        distance = 0.15 * min(4.0, abs(origin - anchor))
        for index in range(4):
            own, other = target_features[index], other_features[index]
            if own is not None and other is not None:
                # Smooth robust distances give pre-release shape substantially
                # more influence than scale, especially the last ramp into launch.
                strength = (0.85, 0.65, 1.15, 0.55)[index]
                distance += strength * math.log1p(((own - other) / 0.9) ** 2)
        other_signal = growth_signal(other_features)
        future = window(grid, 5, 24)
        if target_signal is not None and other_signal is not None and future is not None:
            # Match combined early momentum, then compare the observed analog
            # growth with the target signal. Both penalties are smooth and
            # bounded in their inputs, so contrasting launches retain weight.
            growth = math.tanh((future - origin) / 1.3)
            distance += 1.8 * math.log1p(((target_signal - other_signal) / 0.7) ** 2)
            # A weak direct consistency term also favors flat launch curves for
            # flat early signals and rising curves for rising early signals.
            distance += 0.8 * math.log1p(((growth - target_signal) / 0.85) ** 2)
        weight = reliability * math.exp(-distance)
        analogs.append((grid, origin, weight))
    result = []
    for hour in range(2, 25):
        samples = []
        for grid, origin, weight in analogs:
            index = hour - 1
            if index in grid:
                samples.append((grid[index] - origin, weight))
            elif index - 1 in grid and index + 1 in grid:
                # Fill only isolated interior gaps; never extrapolate an analog.
                samples.append(((grid[index - 1] + grid[index + 1]) / 2 - origin,
                                weight * 0.7))
        prior = fallback_shape(hour)
        if samples:
            center = weighted_quantile(samples, 0.5)
            lower = weighted_quantile(samples, 0.25)
            # A higher percentile sees explosive analog outcomes that the IQR
            # misses. Normalize by its normal score to retain a comparable scale.
            tail_level = 0.90
            upper = weighted_quantile(samples, tail_level)
            total = math.fsum(weight for _, weight in samples)
            effective = total * total / math.fsum(weight * weight for _, weight in samples)
            # Four analogs' worth of prior stabilize tiny or very sparse pools.
            trust = effective / (effective + 4.0)
            center = trust * center + (1 - trust) * prior
            # Normal IQR conversion is robust to extreme launches. Keep separate
            # upper/lower scales to preserve skew in multiplicative growth.
            low_scale = max(0.0, (weighted_quantile(samples, 0.5) - lower) / LEVEL_Z[3])
            high_scale = max(0.0, (upper - weighted_quantile(samples, 0.5)) /
                             statistics.NormalDist().inv_cdf(tail_level))
            # A modest irreducible error grows with horizon. Small samples also
            # retain more of a broad prior, avoiding unjustified narrow bands.
            floor = 0.18 + 0.13 * math.sqrt((hour - 1) / 23)
            low_scale = math.sqrt(trust * low_scale ** 2 + (1 - trust) * 0.65 ** 2 + floor ** 2)
            high_scale = math.sqrt(trust * high_scale ** 2 + (1 - trust) * 0.65 ** 2 + floor ** 2)
            # A smooth shoulder centered in the middle of the release day adds
            # modest coverage correction; the horizon factor phases it in.
            widening = 1 + 0.14 * (1 - math.exp(-(hour - 1) / 5.0)) * math.exp(
                -0.5 * ((hour - 14) / 6.5) ** 2)
            low_scale *= widening
            high_scale *= widening
        else:
            center = prior
            low_scale = high_scale = 0.75 + 0.20 * (hour - 1) / 23
        quantiles = []
        for z in LEVEL_Z:
            spread = low_scale if z < 0 else high_scale
            quantiles.append(math.exp(max(0.0, min(LOG_MAX, anchor + center + z * spread))))
        # Floating-point exp(log(cap)) can slightly exceed the literal cap.
        quantiles = [max(1.0, min(1_000_000_000.0, value)) for value in quantiles]
        result.append({"hour": hour, "q": quantiles})
    return {"hours": result}


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(output_dir, exist_ok=True)
    for name in sorted(os.listdir(input_dir)):
        if not name.endswith(".in"):
            continue
        try:
            with open(os.path.join(input_dir, name), "r", encoding="utf-8") as handle:
                case = json.load(handle)
            prediction = forecast(case)
        except Exception:
            # Malformed or unexpectedly typed case data still receive a complete
            # positive, finite forecast using the same qualitative release prior.
            prediction = forecast({})
        with open(os.path.join(output_dir, name[:-3] + ".out"), "w", encoding="utf-8") as handle:
            json.dump(prediction, handle, ensure_ascii=True, allow_nan=False,
                      separators=(",", ":"), sort_keys=True)
            handle.write("\n")


if __name__ == "__main__":
    main()

# Release-day Twitch forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/HGS11/gta6-twitch-ccu-forecast-01/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/HGS11/gta6-twitch-ccu-forecast-01/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

This self-contained Python program forecasts hourly Twitch viewers for hours
2 through 24 of a game's release day, with five quantiles per hour.

Each comparable launch contributes a release-day curve normalized by its first
hour. A weighted median in log space supplies the typical growth curve, scaled
by the target game's observed first hour. Weights modestly favor similar launch
scale, pre-release uplift, and pre-release trend. Robust lower and upper spreads
across analogs determine uncertainty, with additional horizon-dependent error.
Pre-release shape and the final ramp into launch strongly influence analog
weights, including whether an analog's later growth fits the early signal.
The upper spread uses a higher analog percentile to capture explosive growth,
and a modest smooth widening improves uncertainty through the middle of the day.
Sparse data blend toward a qualitative staggered-unlock and daytime-peak prior.
Missing observations and malformed cases receive complete fallback forecasts.

Run with Python 3.13 and the standard library:

```
MAKEFUN_INPUT_DIR=/path/to/input MAKEFUN_OUTPUT_DIR=/path/to/output python3 -P -s main.py
```

The directories default to `/input` and `/output`. Every input filename ending
in `.in` is processed in sorted order and produces a matching `.out` JSON file.
The program operates offline and deterministically.

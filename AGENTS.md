# AGENTS.md

Guidance for AI agents working in this repository.

## Project overview

A Python Discord bot that checks the latest NOAA HRRR forecast for sailing windows in Port
Townsend Bay and posts Discord embeds when a good window is found. "Good" = wind 5-10 kt,
cloud cover < 20%, precip < 0.1 mm/hr, window **strictly longer** than `MIN_WINDOW_HOURS`
(3), and within daylight hours. Each embed includes per-hour wind/cloud/rain, air temp
(HRRR), water temp + flood/ebb tide (NOAA CO-OPS station 9444900).

## Commands

```bash
source .venv/bin/activate          # ALWAYS use the venv, never system python
pip install -r requirements.txt
python bot.py --once               # full HRRR check, prints windows, no Discord
python bot.py --test-post          # sends a sample embed to the Discord channel
python bot.py --debug-perms        # prints guilds/channels + bot permissions
python bot.py                      # run the hourly Discord loop (foreground)
sudo ./install_service.sh          # install as systemd service + logrotate
systemctl status discord-weather-bot
tail -f logs/bot.log               # service logs (rotated daily by logrotate)
```

There is no test suite; verification is done by running `--once` (needs network, downloads
~300 MB) or `--test-post`. After any change, at minimum run `python bot.py --once` (or the
affected mode) to confirm nothing broke.

## Files

- `bot.py` — entry point. `WeatherBot` (hourly scheduler at :`CHECK_MINUTE`), `TestPoster`
  (`--test-post`), `PermChecker` (`--debug-perms`), `run_once` (`--once`). Reads `--once`,
  `--test-post`, `--debug-perms`, `--verbose`.
- `weather.py` — HRRR fetch (`build_forecast`, `fetch_point_hour` with multi-cycle
  backtrack), `find_windows`, `fetch_marine_data` (CO-OPS water temp + tides),
  `is_daylight` (astral), `clean_cache`.
- `messaging.py` — `build_embed` builds the Discord embed.
- `config.py` — `Settings.from_env()`. Calls `load_dotenv()` on import, so `.env` is read
  from the current working directory.
- `install_service.sh` — writes the systemd unit (runs bot.py, restarts on failure) and a
  logrotate config; requires root.
- `.env` — real `DISCORD_TOKEN` and `DISCORD_CHANNEL_ID`; **never commit or print it**.
- `state.json` — set of announced window keys so each window posts only once.

## Hard-won gotchas (do not regress)

- **Herbie 2026.3.0**: datetimes passed to Herbie must be naive UTC
  (`.replace(tzinfo=None)`); `Herbie.xarray()` returns a **list** of datasets; HRRR `sfc`
  has **no scalar `WIND`** — compute speed from `UGRD`/`VGRD:10 m above ground`. `TCDC`
  is `TCDC:entire atmosphere`, precip is `PRATE:surface`, air temp is
  `TMP:2 m above ground`.
- **astral 3.2**: use `Observer(latitude=..., longitude=...)` with
  `sunrise(obs, date=..., tzinfo=...)`, not `LocationInfo`.
- Timezones: forecast hours are UTC; messages use `TIMEZONE` (America/Los_Angeles).
  Windows are keyed by UTC start ISO string in `state.json`.
- NOAA CO-OPS returns data in UTC. Tide labels (Flood/Ebb) are informational only and do
  not affect window filtering.
- GRIB downloads are cached under `cache/` and removed by `clean_cache` after each run.

## Conventions

- Python 3.11, no type-checker/linter config in the repo — just keep `import` ordering and
  existing style consistent. Add **no comments** unless asked.
- Don't commit `.env`, `cache/`, `logs/`, `state.json`, or `.venv/` (all gitignored).

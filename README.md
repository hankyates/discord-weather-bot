# Discord Weather Window Bot

Posts to Discord when the latest [HRRR](https://rapidrefresh.noaa.gov/hrrr/) forecast shows good sailing weather for Port Townsend Bay: wind **5-10 kt**, **sunny** (< 20% clouds), and **no rain** (< 0.1 mm/hr), for a window that lasts **longer than 3 hours** and falls **within daylight hours**. Each post also includes the **air temperature**, **water temperature**, and **tidal state (flood/ebb)** for the window. It checks every 8 hours, scans the full 48-hour forecast, and announces each good-weather window once, with the times in Pacific time.

## Setup

1. **Create the Discord bot** and invite it to your server (see [discord.com/developers/applications](https://discord.com/developers/applications)):
   - New Application -> Bot -> Reset Token, copy the token.
   - OAuth2 -> URL Generator -> scope `bot` + permission `Send Messages` -> open the URL to invite it.
   - Enable Developer Mode in Discord settings, right-click the target channel, Copy Channel ID.

2. **Configure** the bot:

   ```bash
   cp .env.example .env
   # edit .env, set DISCORD_TOKEN and DISCORD_CHANNEL_ID
   ```

3. **Install and run**:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python bot.py
   ```

## Run as a systemd service

Installs the bot as a service so it keeps running in the background and starts automatically on reboot:

```bash
sudo ./install_service.sh
systemctl status discord-weather-bot
journalctl -u discord-weather-bot -f   # follow live logs
```

The service runs `python bot.py` from the project directory as the user who ran the installer, reads the token from `.env`, and restarts automatically if it crashes. Output goes to `logs/bot.log`, which is rotated **daily** by a `logrotate` config installed at `/etc/logrotate.d/discord-weather-bot` (keeps 14 compressed copies).

## Test without Discord

Fetches the latest HRRR cycle, evaluates every forecast hour, and prints any windows:

```bash
python bot.py --once
```

## Test the Discord integration

Sends a sample embed to the configured channel so you can confirm the token, invite, and permissions work before waiting for a real window:

```bash
python bot.py --test-post
```

If you see `403 Missing Access`, the bot is not in the server or lacks `Send Messages` permission:
invite it via **OAuth2 -> URL Generator** (scope `bot`, permission `Send Messages`) and verify the
channel ID was copied with Developer Mode enabled.

## How it works

- Uses `herbie-data` to grab the most recent HRRR surface cycle (AWS S3 first, NOMADS fallback).
- For each of the next `HORIZON_HOURS` (default 48), extracts the HRRR grid point nearest `LAT`/`LON` and reads 10 m `WIND`, `TCDC` (total cloud cover), and `PRATE` (precipitation rate).
- Marks each hour good when all conditions pass (plus daylight, if `REQUIRE_DAYLIGHT` is on), merges consecutive good hours into windows, and only keeps windows lasting **strictly longer** than `MIN_WINDOW_HOURS`.
- Adds air temperature (HRRR 2 m) per hour, plus current water temperature and predicted flood/ebb tide state for the window from NOAA CO-OPS at `COOPS_STATION` (default `9444900`, Port Townsend). These are informational only and do not affect filtering.
- `check_minute` (default 50) and `check_interval_hours` (default 8) control when the check runs: at `check_minute` past the hour, on hours divisible by `check_interval_hours` (e.g. `08:50`, `16:50`, `00:50` Pacific).
- The forecast is re-downloaded only when a newer HRRR cycle is available. The latest per-hour results are cached in `forecast_cache.json`; if the newest cycle is unchanged (e.g. after a restart or delayed HRRR), the cached hours are reused and nothing is re-posted (each window is still announced only once via `state.json`).
- Announced windows are recorded in `state.json`, so each window is posted once, not re-spammed every cycle.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | | Bot token |
| `DISCORD_CHANNEL_ID` | | Channel to post in |
| `LAT` / `LON` | `48.05` / `-122.77` | Point to evaluate (Port Townsend Bay) |
| `WIND_MIN_KT` / `WIND_MAX_KT` | `5` / `10` | Wind range |
| `CLOUD_MAX_PCT` | `20` | Max cloud cover for "sunny" |
| `RAIN_MAX_MMHR` | `0.1` | Max precip rate for "no rain" |
| `COOPS_STATION` | `9444900` | NOAA CO-OPS station for water temp + tides (Port Townsend) |
| `HORIZON_HOURS` | `48` | Forecast hours to scan |
| `MIN_WINDOW_HOURS` | `3` | Window must last strictly longer than this (default `3` -> 4+ hours) |
| `REQUIRE_DAYLIGHT` | `true` | Only report windows within sunrise-to-sunset hours |
| `TIMEZONE` | `America/Los_Angeles` | Message timezone |
| `CHECK_MINUTE` | `50` | Minute of each check |
| `CHECK_INTERVAL_HOURS` | `8` | Run a check every N hours (aligned to the clock) |
| `FORECAST_CACHE_FILE` | `forecast_cache.json` | Persisted per-hour forecast points (skips re-download/re-post when the HRRR cycle hasn't changed) |

## Notes

- HRRR is a 3 km grid; the nearest grid point to the bay may be over land, which slightly affects the surface wind.
- Each check downloads the wind, cloud, and precipitation fields for all forecast hours from NOAA's AWS bucket (a few hundred MB, a few minutes). GRIB files are removed after each run; only a small temporary index cache is kept and cleaned up.

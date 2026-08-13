from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    discord_token: str = ""
    discord_channel_id: int = 0
    lat: float = 48.05
    lon: float = -122.77
    wind_min_kt: float = 5.0
    wind_max_kt: float = 10.0
    cloud_max_pct: float = 20.0
    rain_max_mmhr: float = 0.1
    min_air_water_sum_f: float = 100.0
    coops_station: str = "9444900"
    horizon_hours: int = 48
    min_window_hours: float = 3.0
    require_daylight: bool = True
    timezone: str = "America/Los_Angeles"
    check_minute: int = 50
    check_interval_hours: int = 8
    cache_dir: Path = Path("cache")
    state_file: Path = Path("state.json")
    forecast_cache_file: Path = Path("forecast_cache.json")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            discord_token=os.getenv("DISCORD_TOKEN", ""),
            discord_channel_id=int(os.getenv("DISCORD_CHANNEL_ID", "0") or 0),
            lat=float(os.getenv("LAT", "48.05")),
            lon=float(os.getenv("LON", "-122.77")),
            wind_min_kt=float(os.getenv("WIND_MIN_KT", "5")),
            wind_max_kt=float(os.getenv("WIND_MAX_KT", "10")),
            cloud_max_pct=float(os.getenv("CLOUD_MAX_PCT", "20")),
            rain_max_mmhr=float(os.getenv("RAIN_MAX_MMHR", "0.1")),
            min_air_water_sum_f=float(os.getenv("MIN_AIR_WATER_SUM_F", "100")),
            coops_station=os.getenv("COOPS_STATION", "9444900"),
            horizon_hours=int(os.getenv("HORIZON_HOURS", "48")),
            min_window_hours=float(os.getenv("MIN_WINDOW_HOURS", "3")),
            require_daylight=os.getenv("REQUIRE_DAYLIGHT", "true").strip().lower()
            in ("1", "true", "yes", "on"),
            timezone=os.getenv("TIMEZONE", "America/Los_Angeles"),
            check_minute=int(os.getenv("CHECK_MINUTE", "50")),
            check_interval_hours=int(os.getenv("CHECK_INTERVAL_HOURS", "8")),
            cache_dir=Path(os.getenv("CACHE_DIR", "cache")),
            state_file=Path(os.getenv("STATE_FILE", "state.json")),
            forecast_cache_file=Path(os.getenv("FORECAST_CACHE_FILE", "forecast_cache.json")),
        )

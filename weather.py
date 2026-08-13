from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as tz
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from astral import Observer
from astral.sun import sunrise, sunset
from herbie import Herbie

from config import Settings

log = logging.getLogger(__name__)

M_PER_S_TO_KT = 1.9438444924406046
KG_M2_S_TO_MM_HR = 3600.0

SEARCH_STRING = r":(?:UGRD|VGRD):10 m above ground|:TCDC:entire atmosphere|:PRATE:surface|:TMP:2 m above ground|:GUST:surface"
MAX_CYCLE_BACKTRACK = 4
CO_OPS_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

_OBSERVERS: dict[tuple[float, float], Observer] = {}

WINDS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def _cardinal(deg: float) -> str:
    idx = int((deg % 360.0) / 22.5 + 0.5) % 16
    return WINDS[idx]


def _point_to_dict(p: HourPoint) -> dict:
    return {
        "valid_time": p.valid_time.isoformat(),
        "wind_kt": p.wind_kt,
        "gust_kt": p.gust_kt,
        "wind_dir": p.wind_dir,
        "cloud_pct": p.cloud_pct,
        "rain_mmhr": p.rain_mmhr,
        "is_good": p.is_good,
        "air_temp_f": p.air_temp_f,
        "tide": p.tide,
    }


def _point_from_dict(d: dict) -> HourPoint:
    return HourPoint(
        valid_time=datetime.fromisoformat(d["valid_time"]),
        wind_kt=d.get("wind_kt"),
        gust_kt=d.get("gust_kt"),
        wind_dir=d.get("wind_dir", ""),
        cloud_pct=d.get("cloud_pct"),
        rain_mmhr=d.get("rain_mmhr"),
        is_good=d.get("is_good", False),
        air_temp_f=d.get("air_temp_f"),
        tide=d.get("tide", ""),
    )


def load_forecast_cache(settings: Settings) -> tuple[datetime, list[HourPoint]] | None:
    path = settings.forecast_cache_file
    try:
        with open(path) as fh:
            data = json.load(fh)
        cycle = datetime.fromisoformat(data["cycle"])
        points = [_point_from_dict(p) for p in data["points"]]
        if not points:
            return None
        return cycle, points
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_forecast_cache(settings: Settings, points: list[HourPoint], cycle: datetime) -> None:
    payload = {
        "cycle": cycle.isoformat(),
        "points": [_point_to_dict(p) for p in points],
    }
    try:
        with open(settings.forecast_cache_file, "w") as fh:
            json.dump(payload, fh, indent=2)
    except OSError:
        log.warning("Could not write forecast cache to %s", settings.forecast_cache_file)


def _observer(settings: Settings) -> Observer:
    key = (settings.lat, settings.lon)
    if key not in _OBSERVERS:
        _OBSERVERS[key] = Observer(latitude=settings.lat, longitude=settings.lon)
    return _OBSERVERS[key]


def is_daylight(settings: Settings, dt_utc: datetime) -> bool:
    zone = ZoneInfo(settings.timezone)
    local = dt_utc.astimezone(zone)
    obs = _observer(settings)
    s = sunrise(obs, date=local.date(), tzinfo=zone)
    e = sunset(obs, date=local.date(), tzinfo=zone)
    if s is None or e is None:
        return True
    return s <= local < e


@dataclass
class HourPoint:
    valid_time: datetime
    wind_kt: float | None
    cloud_pct: float | None
    rain_mmhr: float | None
    is_good: bool = False
    gust_kt: float | None = None
    wind_dir: str = ""
    air_temp_f: float | None = None
    tide: str = ""


@dataclass
class WeatherWindow:
    start: datetime
    end: datetime
    hours: list[HourPoint]


def find_latest_cycle(settings: Settings) -> datetime | None:
    now = datetime.now(tz.utc).replace(minute=0, second=0, microsecond=0).replace(tzinfo=None)
    for back in range(0, 6):
        cycle = now - timedelta(hours=back)
        try:
            h = Herbie(
                date=cycle,
                model="hrrr",
                product="sfc",
                fxx=1,
                priority=["aws"],
                verbose=False,
                save_dir=settings.cache_dir,
            )
        except Exception:
            continue
        if h.grib is not None:
            return cycle
    return None


def _nearest_index(ds, lat_target: float, lon_target: float) -> tuple[int, int]:
    lat = np.asarray(ds.latitude.values, dtype=float)
    lon = np.asarray(ds.longitude.values, dtype=float)
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    dist = (lat - lat_target) ** 2 + (lon - lon_target) ** 2
    return tuple(int(i) for i in np.unravel_index(np.argmin(dist), dist.shape))


def _find_var(ds, keys: list[str]) -> str | None:
    for name, da in ds.data_vars.items():
        haystack = f"{name} {getattr(da, 'long_name', '')}"
        if all(key.lower() in haystack.lower() for key in keys):
            return name
    return None


def _find_da(datasets, keys: list[str]):
    for ds in datasets:
        name = _find_var(ds, keys)
        if name is not None:
            return ds[name]
    return None


def _to_wind_kt(da, idx: tuple[int, int]) -> float:
    value = float(da.values[idx])
    units = str(getattr(da, "units", "")).lower()
    if "m" in units:
        return value * M_PER_S_TO_KT
    return value


def _to_cloud_pct(da, idx: tuple[int, int]) -> float:
    value = float(da.values[idx])
    units = str(getattr(da, "units", "")).lower()
    if "1" in units or "%" in units:
        return value
    return value * 100.0


def _to_rain_mmhr(da, idx: tuple[int, int]) -> float:
    value = float(da.values[idx])
    units = str(getattr(da, "units", "")).lower()
    if "kg" in units:
        return value * KG_M2_S_TO_MM_HR
    return value


def _to_air_temp_f(da, idx: tuple[int, int]) -> float:
    value = float(da.values[idx])
    units = str(getattr(da, "units", "")).lower()
    if "k" in units:
        return (value - 273.15) * 9.0 / 5.0 + 32.0
    if "c" in units:
        return value * 9.0 / 5.0 + 32.0
    return value


def _extract_point(
    datasets, settings: Settings
) -> tuple[float | None, float | None, float | None, float | None, float | None, str]:
    if not datasets:
        return None, None, None, None, None, ""
    idx = _nearest_index(datasets[0], settings.lat, settings.lon)
    u = _find_da(datasets, ["u wind component"])
    if u is None:
        u = _find_da(datasets, ["u10"])
    v = _find_da(datasets, ["v wind component"])
    if v is None:
        v = _find_da(datasets, ["v10"])
    cloud = _find_da(datasets, ["total cloud"])
    if cloud is None:
        cloud = _find_da(datasets, ["tcc"])
    rain = _find_da(datasets, ["precip"])
    if rain is None:
        rain = _find_da(datasets, ["prate"])
    if u is None or v is None or cloud is None or rain is None:
        return None, None, None, None, None, ""
    u_kt = _to_wind_kt(u, idx)
    v_kt = _to_wind_kt(v, idx)
    wind_kt = (u_kt**2 + v_kt**2) ** 0.5
    gust = _find_da(datasets, ["gust"])
    gust_kt = _to_wind_kt(gust, idx) if gust is not None else None
    wind_dir = _cardinal(math.degrees(math.atan2(-u_kt, -v_kt)))
    air = _find_da(datasets, ["temperature"])
    air_f = _to_air_temp_f(air, idx) if air is not None else None
    return (
        wind_kt,
        _to_cloud_pct(cloud, idx),
        _to_rain_mmhr(rain, idx),
        air_f,
        gust_kt,
        wind_dir,
    )


def _evaluate_good(settings: Settings, point: HourPoint, water_temp_f: float | None) -> bool:
    if not (settings.wind_min_kt <= point.wind_kt <= settings.wind_max_kt):
        return False
    if point.cloud_pct >= settings.cloud_max_pct:
        return False
    if point.rain_mmhr >= settings.rain_max_mmhr:
        return False
    if settings.require_daylight and not is_daylight(settings, point.valid_time):
        return False
    if point.air_temp_f is None or water_temp_f is None:
        return False
    return point.air_temp_f + water_temp_f > settings.min_air_water_sum_f


def fetch_point_hour(settings: Settings, latest: datetime, target_fxx: int) -> HourPoint | None:
    for back in range(0, MAX_CYCLE_BACKTRACK):
        cycle = latest - timedelta(hours=back)
        fxx = target_fxx + back
        if fxx < 0:
            continue
        try:
            h = Herbie(
                date=cycle,
                model="hrrr",
                product="sfc",
                fxx=fxx,
                priority=["aws", "nomads"],
                verbose=False,
                save_dir=settings.cache_dir,
            )
        except Exception as exc:
            log.debug("F%02d: error locating %s F%02d: %s", target_fxx, cycle, fxx, exc)
            continue
        if h.grib is None:
            log.info(
                "F%02d: %s F%02d not available on %s",
                target_fxx, cycle.strftime("%m-%d %H"), fxx, h.grib_source or "any source",
            )
            continue
        log.info(
            "F%02d: downloading %s F%02d from %s",
            target_fxx, cycle.strftime("%m-%d %H"), fxx, h.grib_source,
        )
        t0 = time.monotonic()
        try:
            ds = h.xarray(SEARCH_STRING, remove_grib=True, verbose=False)
        except Exception as exc:
            log.warning("F%02d: download/decode failed for %s F%02d: %s", target_fxx, cycle, fxx, exc)
            continue
        elapsed = time.monotonic() - t0
        datasets = ds if isinstance(ds, list) else [ds]
        wind, cloud, rain, air_f, gust_kt, wind_dir = _extract_point(datasets, settings)
        if wind is None:
            log.warning("F%02d: could not extract point data from %s F%02d", target_fxx, cycle, fxx)
            continue
        valid_time = _as_utc(datasets[0].valid_time.values)
        log.info(
            "F%02d: done in %.1fs -> valid %s UTC: wind=%4.1f kt gust=%4.1f kt %s cloud=%4.0f%% rain=%5.3f mm/hr air=%4.0fF",
            target_fxx, elapsed, valid_time.strftime("%m-%d %H:%M"), wind,
            gust_kt if gust_kt is not None else float("nan"), wind_dir, cloud, rain,
            air_f if air_f is not None else float("nan"),
        )
        point = HourPoint(
            valid_time=valid_time,
            wind_kt=wind,
            gust_kt=gust_kt,
            wind_dir=wind_dir,
            cloud_pct=cloud,
            rain_mmhr=rain,
            air_temp_f=air_f,
        )
        point.is_good = _evaluate_good(settings, point, None)
        return point
    log.warning("F%02d: no data found in any recent cycle", target_fxx)
    return None


def _as_utc(value) -> datetime:
    dt = pd.to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz.utc)
    else:
        dt = dt.tz_convert(tz.utc)
    return dt.to_pydatetime()


def _coops_request(params: dict) -> dict | None:
    try:
        r = requests.get(CO_OPS_URL, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        log.warning("CO-OPS API request failed: %s", params.get("product"))
        return None


def _fetch_water_temp(settings: Settings) -> float | None:
    data = _coops_request(
        {
            "station": settings.coops_station,
            "date": "latest",
            "product": "water_temperature",
            "units": "english",
            "time_zone": "lst_ldt",
            "format": "json",
        }
    )
    if not data or not data.get("data"):
        return None
    try:
        return float(data["data"][0]["v"])
    except (KeyError, TypeError, ValueError):
        return None


def _fetch_tide_events(settings: Settings, start: datetime, end: datetime) -> list[tuple[datetime, str]]:
    zone = ZoneInfo(settings.timezone)
    data = _coops_request(
        {
            "station": settings.coops_station,
            "begin_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "product": "predictions",
            "datum": "MLLW",
            "interval": "hilo",
            "units": "english",
            "time_zone": "lst_ldt",
            "format": "json",
        }
    )
    events: list[tuple[datetime, str]] = []
    if data and data.get("predictions"):
        for row in data["predictions"]:
            try:
                local = datetime.strptime(row["t"], "%Y-%m-%d %H:%M").replace(tzinfo=zone)
                events.append((local.astimezone(tz.utc), row["type"]))
            except (KeyError, ValueError):
                continue
    events.sort(key=lambda e: e[0])
    return events


def _tide_state(events: list[tuple[datetime, str]], dt_utc: datetime) -> str:
    for event_time, typ in events:
        if event_time >= dt_utc:
            return "Flood" if typ == "H" else "Ebb"
    return ""


def fetch_marine_data(settings: Settings, points: list[HourPoint]) -> float | None:
    water_temp = _fetch_water_temp(settings)
    if not points:
        return water_temp
    for point in points:
        point.is_good = _evaluate_good(settings, point, water_temp)
    start = min(p.valid_time for p in points)
    end = max(p.valid_time for p in points) + timedelta(days=1)
    events = _fetch_tide_events(settings, start, end)
    for point in points:
        point.tide = _tide_state(events, point.valid_time)
    return water_temp


def clean_cache(settings: Settings) -> None:
    if not settings.cache_dir.exists():
        return
    for path in sorted(settings.cache_dir.rglob("*"), reverse=True):
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir() and path != settings.cache_dir:
                path.rmdir()
        except OSError:
            pass


def build_forecast(settings: Settings) -> tuple[list[HourPoint], datetime | None]:
    latest = find_latest_cycle(settings)
    if latest is None:
        log.warning("No HRRR cycle available")
        return [], None
    cached = load_forecast_cache(settings)
    if cached is not None:
        cache_cycle, cached_points = cached
        if cache_cycle == latest and cached_points:
            log.info(
                "Forecast unchanged: reusing cached %d hours for cycle %s UTC",
                len(cached_points), latest.isoformat(),
            )
            return cached_points, latest
    log.info("Latest HRRR cycle: %s UTC", latest.isoformat())
    total = settings.horizon_hours + 1
    points: list[HourPoint] = []
    for fxx in range(0, settings.horizon_hours + 1):
        log.info("Fetching forecast hour F%02d (%d/%d)", fxx, fxx + 1, total)
        point = fetch_point_hour(settings, latest, fxx)
        if point is not None:
            points.append(point)
    points.sort(key=lambda p: p.valid_time)
    log.info("Fetched %d/%d forecast hours", len(points), total)
    if points:
        save_forecast_cache(settings, points, latest)
    return points, latest


def find_windows(settings: Settings, points: list[HourPoint]) -> list[WeatherWindow]:
    windows: list[WeatherWindow] = []
    current: list[HourPoint] = []

    def flush() -> None:
        nonlocal current
        if current:
            windows.append(
                WeatherWindow(
                    start=current[0].valid_time,
                    end=current[-1].valid_time,
                    hours=current,
                )
            )
            current = []

    for point in points:
        if point.is_good:
            current.append(point)
        else:
            flush()
    flush()

    min_hours = settings.min_window_hours
    return [
        w
        for w in windows
        if ((w.end - w.start).total_seconds() / 3600.0) + 1.0 > min_hours
    ]

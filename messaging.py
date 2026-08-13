from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from config import Settings
from weather import HourPoint, WeatherWindow

TITLE = "Port Townsend Bay - Good Weather Window"
COLOR = 0x00B8A9
MAX_FIELD_CHARS = 1024


def _tz(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _format_hour(hour: HourPoint, settings: Settings) -> str:
    local = hour.valid_time.astimezone(_tz(settings))
    sun = "sunny" if hour.cloud_pct < settings.cloud_max_pct else "cloudy"
    rain = "dry" if hour.rain_mmhr < settings.rain_max_mmhr else f"{hour.rain_mmhr:.1f} mm"
    wind = (
        f"{hour.wind_kt:.0f}-{hour.gust_kt:.0f} kt"
        if hour.gust_kt is not None
        else f"{hour.wind_kt:.0f} kt"
    )
    parts = [f"{local:%I:%M %p}", wind]
    if hour.wind_dir:
        parts.append(hour.wind_dir)
    parts.extend([sun, rain])
    if hour.air_temp_f is not None:
        parts.append(f"{hour.air_temp_f:.0f}F")
    if hour.tide:
        parts.append(hour.tide)
    return "  ".join(parts)


def _format_window(window: WeatherWindow, settings: Settings) -> str:
    start = window.start.astimezone(_tz(settings))
    end = window.end.astimezone(_tz(settings))
    if start.date() == end.date():
        return f"{start:%a, %b %d}  {start:%I:%M %p} - {end:%I:%M %p}"
    return f"{start:%a, %b %d %I:%M %p} - {end:%a, %b %d %I:%M %p}"


def _chunk_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) + 1 > MAX_FIELD_CHARS:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def build_embed(
    settings: Settings,
    windows: list[WeatherWindow],
    cycle_utc: datetime | None,
    now_local: datetime,
    water_temp_f: float | None = None,
) -> discord.Embed:
    embed = discord.Embed(title=TITLE, color=COLOR)
    lines = [
        "Conditions: wind **{min:.0f}-{max:.0f} kt** | sunny (<{cloud:.0f}% clouds) "
        "| no rain (<{rain:.1f} mm/hr) | air+water temp **> {sum:.0f}F**".format(
            min=settings.wind_min_kt,
            max=settings.wind_max_kt,
            cloud=settings.cloud_max_pct,
            rain=settings.rain_max_mmhr,
            sum=settings.min_air_water_sum_f,
        ),
        f"Window must last longer than {settings.min_window_hours:.0f} hours"
        + (" and fall within daylight hours" if settings.require_daylight else ""),
    ]
    if water_temp_f is not None:
        lines.append(f"Water temp: **{water_temp_f:.1f}F**")
    lines.append(f"Times in **{settings.timezone}**")
    embed.description = "\n".join(lines)

    if not windows:
        embed.add_field(
            name="No good windows",
            value=f"No matching windows within {settings.horizon_hours} hours.",
            inline=False,
        )
    else:
        for window in windows:
            lines = [_format_hour(h, settings) for h in window.hours]
            chunks = _chunk_lines(lines)
            for i, chunk in enumerate(chunks):
                name = _format_window(window, settings)
                if len(chunks) > 1:
                    name = f"{name} (part {i + 1}/{len(chunks)})"
                embed.add_field(name=name, value=f"```{chunk}```", inline=False)

    if cycle_utc is not None:
        embed.set_footer(
            text=f"HRRR cycle {cycle_utc:%Y-%m-%d %HZ} UTC | updated {now_local:%I:%M %p}"
        )
    return embed

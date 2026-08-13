from __future__ import annotations

import argparse
import asyncio
import json
import logging
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord

import weather
from config import Settings
from messaging import build_embed
from weather import HourPoint, WeatherWindow

log = logging.getLogger("weather_bot")


def sample_windows(settings: Settings) -> list[WeatherWindow]:
    now = datetime.now(ZoneInfo("UTC")).replace(minute=0, second=0, microsecond=0)
    tides = ["Flood", "Flood", "Ebb", "Ebb", "Ebb"]
    hours = [
        HourPoint(
            valid_time=now + timedelta(hours=i),
            wind_kt=7.0,
            gust_kt=13.0,
            wind_dir="NNW",
            cloud_pct=5.0,
            rain_mmhr=0.0,
            is_good=True,
            air_temp_f=62.0 + i,
            tide=tides[i % len(tides)],
        )
        for i in range(5)
    ]
    return [WeatherWindow(start=hours[0].valid_time, end=hours[-1].valid_time, hours=hours)]


class TestPoster(discord.Client):
    def __init__(self, settings: Settings) -> None:
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        sample = sample_windows(settings)
        sample_points = [h for w in sample for h in w.hours]
        water_temp = weather.fetch_marine_data(settings, sample_points)
        self.embed = build_embed(
            settings,
            sample,
            cycle_utc=datetime.now(ZoneInfo("UTC")).replace(minute=0, second=0, microsecond=0),
            now_local=datetime.now(ZoneInfo(settings.timezone)),
            water_temp_f=water_temp,
        )

    async def on_ready(self) -> None:
        log.info("Logged in as %s", self.user)
        try:
            channel = self.get_channel(self.settings.discord_channel_id)
            if channel is None:
                log.error(
                    "Channel %s not found. Is the bot invited to the server and is the channel ID correct?",
                    self.settings.discord_channel_id,
                )
            else:
                await channel.send(embed=self.embed)
                log.info("Sent test embed to #%s", channel.name)
        except discord.Forbidden:
            log.error(
                "403 Missing Access: the bot is in the server but cannot send to channel %s. "
                "Grant it the Send Messages permission for that channel.",
                self.settings.discord_channel_id,
            )
        except Exception as exc:
            log.error("Failed to send test embed: %s", exc)
        finally:
            await self.close()


class PermChecker(discord.Client):
    def __init__(self, settings: Settings) -> None:
        super().__init__(intents=discord.Intents.default())
        self.settings = settings

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id)
        log.info("Guilds the bot is in (%d):", len(self.guilds))
        for guild in self.guilds:
            log.info("  - %s (id=%s)", guild.name, guild.id)
        target = self.settings.discord_channel_id
        channel = self.get_channel(target)
        if channel is None:
            log.error("Channel %s was NOT found in any guild this bot is in.", target)
        else:
            guild = channel.guild
            log.info("Channel %s -> #%s in guild '%s' (id=%s)", target, channel.name, guild.name, guild.id)
            log.info("Channel type: %s", type(channel).__name__)
            try:
                perms = channel.permissions_for(guild.me)
                log.info("Bot's effective permissions in this channel:")
                log.info("  view_channel:            %s", perms.view_channel)
                log.info("  send_messages:           %s", perms.send_messages)
                log.info("  embed_links:             %s", perms.embed_links)
                log.info("  read_message_history:    %s", perms.read_message_history)
                log.info("  manage_channels:         %s", perms.manage_channels)
            except Exception as exc:
                log.error("Could not compute permissions: %s", exc)
        await self.close()


def load_announced(path: Path) -> set[str]:
    try:
        with open(path) as fh:
            return set(json.load(fh).get("announced", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_announced(path: Path, announced: set[str]) -> None:
    with open(path, "w") as fh:
        json.dump({"announced": sorted(announced)}, fh, indent=2)


async def run_once(settings: Settings) -> None:
    points, cycle_utc = weather.build_forecast(settings)
    weather.clean_cache(settings)
    if not points:
        log.error("No forecast data could be fetched.")
        return
    water_temp = weather.fetch_marine_data(settings, points)
    windows = weather.find_windows(settings, points)
    now_local = datetime.now(ZoneInfo(settings.timezone))

    good_count = sum(1 for p in points if p.is_good)
    log.info("Evaluated %d hours, %d meeting conditions, %d windows found", len(points), good_count, len(windows))
    if windows:
        embed = build_embed(settings, windows, cycle_utc, now_local, water_temp)
        print("")
        print(embed.title)
        print(embed.description)
        for field in embed.fields:
            print(f"[{field.name}]")
            print(field.value)
    else:
        print("No good-weather windows found within the forecast horizon.")


class WeatherBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        self.announced = load_announced(settings.state_file)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id)
        self.bg_task = asyncio.create_task(self.scheduler())

    async def scheduler(self) -> None:
        zone = ZoneInfo(self.settings.timezone)
        interval = self.settings.check_interval_hours
        while True:
            now = datetime.now(zone)
            next_run = now.replace(minute=self.settings.check_minute, second=0, microsecond=0)
            next_run = next_run.replace(hour=(next_run.hour // interval) * interval)
            if next_run <= now:
                next_run += timedelta(hours=interval)
            log.info("Next check scheduled for %s", next_run.isoformat())
            await asyncio.sleep((next_run - now).total_seconds())
            try:
                await self.check_and_post()
            except Exception:
                log.exception("Weather check failed")

    async def check_and_post(self) -> None:
        settings = self.settings

        def fetch_sync() -> tuple[list[HourPoint], datetime | None, list[WeatherWindow], float | None]:
            points, cycle_utc = weather.build_forecast(settings)
            weather.clean_cache(settings)
            if not points:
                return [], None, [], None
            water_temp = weather.fetch_marine_data(settings, points)
            windows = weather.find_windows(settings, points)
            return points, cycle_utc, windows, water_temp

        points, cycle_utc, windows, water_temp = await asyncio.to_thread(fetch_sync)
        if not points:
            log.warning("No forecast data available; skipping.")
            return
        now_utc = datetime.now(ZoneInfo("UTC"))
        future_windows = [w for w in windows if w.end > now_utc]
        if not future_windows:
            log.info("No upcoming good windows.")
            return

        channel = self.get_channel(settings.discord_channel_id)
        if channel is None:
            log.error("Channel %s not found (is the bot invited to it?).", settings.discord_channel_id)
            return

        now_local = datetime.now(ZoneInfo(settings.timezone))
        for window in future_windows:
            key = window.start.isoformat()
            if key in self.announced:
                log.info("Window already announced: %s", key)
                continue
            embed = build_embed(settings, [window], cycle_utc, now_local, water_temp)
            await channel.send(embed=embed)
            log.info("Announced window: %s", key)
            self.announced.add(key)
            save_announced(settings.state_file, self.announced)


def main() -> None:
    parser = argparse.ArgumentParser(description="HRRR weather-window Discord bot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fetch the latest HRRR forecast, print results, and exit (no Discord).",
    )
    parser.add_argument(
        "--test-post",
        action="store_true",
        help="Send a sample embed to the configured channel to verify the Discord integration.",
    )
    parser.add_argument(
        "--debug-perms",
        action="store_true",
        help="Show which guilds/channels the bot can access and its permissions there.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("herbie").setLevel(logging.WARNING)
    logging.getLogger("cfgrib").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", message=".*regular expression.*")
    warnings.filterwarnings("ignore", message=".*future version of xarray.*")

    settings = Settings.from_env()
    if args.once:
        asyncio.run(run_once(settings))
        return

    if not settings.discord_token or not settings.discord_channel_id:
        log.error("DISCORD_TOKEN and DISCORD_CHANNEL_ID must be set in .env to run the bot.")
        raise SystemExit(1)

    if args.test_post:
        poster = TestPoster(settings)
        poster.run(settings.discord_token, log_handler=None)
        return

    if args.debug_perms:
        checker = PermChecker(settings)
        checker.run(settings.discord_token, log_handler=None)
        return

    bot = WeatherBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

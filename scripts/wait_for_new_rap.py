from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

NO_NEW_CYCLE_EXIT = 3


def parse_args():
    parser = argparse.ArgumentParser(
        description="Wait until a RAP f00 cycle newer than the published guidance is available."
    )
    parser.add_argument("--published-url", required=True)
    parser.add_argument("--poll-seconds", type=int, default=120)
    parser.add_argument("--max-wait-minutes", type=int, default=24)
    parser.add_argument("--max-lookback-hours", type=int, default=8)
    parser.add_argument("--cache-dir", type=Path, default=Path("cache"))
    return parser.parse_args()


def _as_utc_datetime(value) -> datetime | None:
    if value in (None, "", "unknown-local-file", "not generated"):
        return None

    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()

    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None

    if not isinstance(value, datetime):
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def published_cycle(url: str) -> datetime | None:
    separator = "&" if "?" in url else "?"
    request = Request(
        f"{url}{separator}t={int(time.time())}",
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "mbcp-rap-cycle-watcher/1.0",
        },
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Could not read published metadata ({type(exc).__name__}: {exc}).")
        return None

    cycle_text = payload.get("cycle", {}).get("cycle_time_utc")
    cycle = _as_utc_datetime(cycle_text)
    if cycle is None:
        print(f"Published metadata did not contain a usable RAP cycle: {cycle_text!r}")
    return cycle


def newest_available_cycle(cache_dir: Path, max_lookback_hours: int) -> datetime:
    from mbcp_guidance.rap import latest_rap_f00

    herbie = latest_rap_f00(
        max_lookback_hours=max_lookback_hours,
        cache_dir=cache_dir,
    )
    cycle = _as_utc_datetime(herbie.date)
    if cycle is None:
        raise RuntimeError(f"Could not interpret Herbie RAP cycle time: {herbie.date!r}")
    return cycle


def fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%MZ") if dt else "none"


def main() -> int:
    args = parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    published = published_cycle(args.published_url)
    print(f"Currently published RAP cycle: {fmt(published)}")

    deadline = datetime.now(timezone.utc) + timedelta(minutes=args.max_wait_minutes)
    attempt = 0

    while True:
        attempt += 1
        now = datetime.now(timezone.utc)

        try:
            available = newest_available_cycle(args.cache_dir, args.max_lookback_hours)
            print(
                f"[{now:%Y-%m-%d %H:%M:%SZ}] Attempt {attempt}: "
                f"newest available RAP cycle is {fmt(available)}"
            )
            if published is None or available > published:
                print(
                    f"New RAP cycle detected: {fmt(available)} "
                    f"(published: {fmt(published)})."
                )
                return 0
        except Exception as exc:
            print(
                f"[{now:%Y-%m-%d %H:%M:%SZ}] RAP availability check failed: "
                f"{type(exc).__name__}: {exc}"
            )

        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            print(
                f"No RAP cycle newer than {fmt(published)} became available "
                f"within {args.max_wait_minutes} minutes."
            )
            return NO_NEW_CYCLE_EXIT

        sleep_seconds = min(args.poll_seconds, max(1, int(remaining)))
        print(f"No newer cycle yet; checking again in {sleep_seconds} seconds.")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    sys.exit(main())

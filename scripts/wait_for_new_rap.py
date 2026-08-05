from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

NO_NEW_CYCLE_EXIT = 3


@dataclass(frozen=True)
class RapProduct:
    cycle: datetime
    valid: datetime
    forecast_hour: int


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Wait until a preferred RAP product valid at the current UTC hour "
            "is newer than the published guidance."
        )
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


def _forecast_hour(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def published_product(url: str) -> RapProduct | None:
    separator = "&" if "?" in url else "?"
    request = Request(
        f"{url}{separator}t={int(time.time())}",
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "mbcp-rap-cycle-watcher/2.0",
        },
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Could not read published metadata ({type(exc).__name__}: {exc}).")
        return None

    cycle_meta = payload.get("cycle", {})
    cycle = _as_utc_datetime(cycle_meta.get("cycle_time_utc"))
    forecast_hour = _forecast_hour(cycle_meta.get("forecast_hour", 0))
    valid = _as_utc_datetime(cycle_meta.get("valid_time_utc"))

    # Backward compatibility with pages generated before valid_time_utc existed.
    if valid is None and cycle is not None:
        valid = cycle + timedelta(hours=forecast_hour)

    if cycle is None or valid is None:
        print(
            "Published metadata did not contain a usable RAP cycle/valid time: "
            f"cycle={cycle_meta.get('cycle_time_utc')!r}, "
            f"valid={cycle_meta.get('valid_time_utc')!r}, "
            f"fhr={cycle_meta.get('forecast_hour')!r}"
        )
        return None

    return RapProduct(cycle=cycle, valid=valid, forecast_hour=forecast_hour)


def preferred_available_product(
    cache_dir: Path,
    max_lookback_hours: int,
) -> RapProduct:
    from mbcp_guidance.rap import latest_rap_valid_now

    herbie, valid_dt = latest_rap_valid_now(
        max_lookback_hours=max_lookback_hours,
        cache_dir=cache_dir,
    )
    cycle = _as_utc_datetime(herbie.date)
    valid = _as_utc_datetime(valid_dt)
    if cycle is None or valid is None:
        raise RuntimeError(
            f"Could not interpret Herbie RAP times: cycle={herbie.date!r}, "
            f"valid={valid_dt!r}"
        )

    return RapProduct(
        cycle=cycle,
        valid=valid,
        forecast_hour=_forecast_hour(getattr(herbie, "fxx", 0)),
    )


def is_newer_preferred_product(
    available: RapProduct,
    published: RapProduct | None,
) -> bool:
    """Return True when the available product should replace the published one."""
    if published is None:
        return True

    # A newer valid hour always supersedes an older valid hour.
    if available.valid != published.valid:
        return available.valid > published.valid

    # For the same valid hour, prefer the newest cycle (lowest forecast hour).
    if available.cycle != published.cycle:
        return available.cycle > published.cycle

    return available.forecast_hour != published.forecast_hour


def fmt_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%MZ") if value else "none"


def fmt_product(product: RapProduct | None) -> str:
    if product is None:
        return "none"
    return (
        f"cycle {fmt_time(product.cycle)} f{product.forecast_hour:02d} "
        f"valid {fmt_time(product.valid)}"
    )


def main() -> int:
    args = parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    published = published_product(args.published_url)
    print(f"Currently published RAP product: {fmt_product(published)}")

    deadline = datetime.now(timezone.utc) + timedelta(minutes=args.max_wait_minutes)
    attempt = 0

    while True:
        attempt += 1
        now = datetime.now(timezone.utc)

        try:
            available = preferred_available_product(
                args.cache_dir,
                args.max_lookback_hours,
            )
            print(
                f"[{now:%Y-%m-%d %H:%M:%SZ}] Attempt {attempt}: "
                f"preferred available RAP product is {fmt_product(available)}"
            )
            if is_newer_preferred_product(available, published):
                print(
                    f"New current-hour-valid RAP product detected: "
                    f"{fmt_product(available)} (published: {fmt_product(published)})."
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
                "No preferred current-hour-valid RAP product newer than "
                f"{fmt_product(published)} became available within "
                f"{args.max_wait_minutes} minutes."
            )
            return NO_NEW_CYCLE_EXIT

        sleep_seconds = min(args.poll_seconds, max(1, int(remaining)))
        print(f"No newer preferred product yet; checking again in {sleep_seconds} seconds.")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    sys.exit(main())

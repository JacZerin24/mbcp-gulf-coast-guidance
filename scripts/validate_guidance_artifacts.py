from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np


EXPECTED_MODEL_VERSION = "research-full-fit-2026-07-v1"
EXPECTED_VARIABLES = [
    "vertical_totals_850_500_c",
    "mlcape_jkg",
    "sbli_c",
    "mid_level_lapse_rate_c_km",
    "sfc_3km_lapse_rate_c_km",
    "dcape_jkg",
    "thetae_deficit_k",
]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class LocalSource:
    def __init__(self, web_dir: Path):
        self.web_dir = web_dir

    def bytes(self, relative_path: str) -> bytes:
        return (self.web_dir / relative_path).read_bytes()

    def json(self, relative_path: str):
        return json.loads(self.bytes(relative_path).decode("utf-8"))


class RemoteSource:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/") + "/"

    def bytes(self, relative_path: str) -> bytes:
        url = urllib.parse.urljoin(self.base_url, relative_path)
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}qc={int(time.time() * 1000)}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "mbcp-guidance-qc/1.0",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    def json(self, relative_path: str):
        return json.loads(self.bytes(relative_path).decode("utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _finite_number(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _validate_cycle(cycle: dict) -> None:
    from datetime import datetime, timezone

    cycle_text = cycle.get("cycle_time_utc")
    valid_text = cycle.get("valid_time_utc")
    forecast_hour = int(cycle.get("forecast_hour", 0))
    _require(cycle_text and valid_text, "cycle and valid times are required")

    if str(cycle_text).startswith("unknown") or str(cycle_text).startswith("not generated"):
        return

    def parse(text: str) -> datetime:
        text = text.replace("Z", "+00:00")
        value = datetime.fromisoformat(text)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    cycle_time = parse(str(cycle_text))
    valid_time = parse(str(valid_text))
    delta_hours = (valid_time - cycle_time).total_seconds() / 3600.0
    _require(
        abs(delta_hours - forecast_hour) < 1.0e-6,
        f"cycle/fhr/valid mismatch: cycle={cycle_text}, f{forecast_hour:02d}, valid={valid_text}",
    )


def _validate_latest(source) -> dict:
    latest = source.json("data/latest.json")
    _require(latest.get("status") != "error", f"latest.json reports error: {latest.get('error_message')}")
    _require(int(latest.get("display_version", -1)) >= 7, "display_version is older than scientific-fidelity release")

    model = latest.get("model") or {}
    _require(model.get("version") == EXPECTED_MODEL_VERSION, f"unexpected model version: {model.get('version')}")
    _require(int(model.get("predictor_count", 0)) == 7, "latest.json predictor_count must be 7")

    fidelity = latest.get("scientific_fidelity") or {}
    _require(fidelity.get("model_version") == EXPECTED_MODEL_VERSION, "scientific_fidelity model version mismatch")

    methods = latest.get("field_methods") or {}
    stats = latest.get("field_stats") or {}
    for key in EXPECTED_VARIABLES:
        _require(key in methods, f"missing field method metadata for {key}")
        _require(bool(methods[key].get("research_definition")), f"missing research definition for {key}")
        _require(key in stats, f"missing field statistics for {key}")
        field_stats = stats[key]
        _require(int(field_stats.get("valid_grid_points", 0)) > 0, f"{key} has no valid grid points")
        for stat_name in ("minimum", "median", "maximum"):
            _require(_finite_number(field_stats.get(stat_name)), f"{key} {stat_name} is not finite")

    _validate_cycle(latest.get("cycle") or {})
    return latest


def _validate_readout(source, latest: dict) -> dict:
    readout_meta = latest.get("readout") or {}
    filename = readout_meta.get("file")
    _require(filename, "latest.json has no readout file")
    grid = source.json(f"data/{filename}")

    model = grid.get("model") or {}
    _require(model.get("version") == EXPECTED_MODEL_VERSION, "readout model version mismatch")
    variables = model.get("variables") or []
    variable_keys = [item.get("key") for item in variables]
    _require(variable_keys == EXPECTED_VARIABLES, f"readout predictor order mismatch: {variable_keys}")

    columns = grid.get("columns") or []
    for required in ["latitude", "longitude", "index", "probability_percent", *EXPECTED_VARIABLES]:
        _require(required in columns, f"readout is missing column {required}")
    col = {name: index for index, name in enumerate(columns)}

    points = grid.get("points") or []
    _require(len(points) > 0, "readout grid contains no valid points")
    _require(int(readout_meta.get("point_count", -1)) == len(points), "readout point_count metadata mismatch")

    intercept = float(model["intercept"])
    variable_meta = {item["key"]: item for item in variables}
    max_probability_error_pp = 0.0
    index_mismatch_count = 0

    for point_number, row in enumerate(points):
        _require(len(row) == len(columns), f"readout row {point_number} has wrong column count")
        for value in row:
            _require(_finite_number(value), f"readout row {point_number} contains a non-finite value")

        stored_index = int(round(float(row[col["index"]])))
        stored_probability_percent = float(row[col["probability_percent"]])
        _require(0 <= stored_index <= 10, f"readout index out of range at row {point_number}")
        _require(0.0 <= stored_probability_percent <= 100.0, f"readout probability out of range at row {point_number}")

        logit = intercept
        for key in EXPECTED_VARIABLES:
            meta = variable_meta[key]
            value = float(row[col[key]])
            z = (value - float(meta["mean"])) / float(meta["std"])
            logit += float(meta["coefficient"]) * z
        probability = 1.0 / (1.0 + math.exp(-logit))
        reconstructed_percent = probability * 100.0
        max_probability_error_pp = max(
            max_probability_error_pp,
            abs(stored_probability_percent - reconstructed_percent),
        )

        expected_index = int(np.clip(np.rint(probability * 10.0), 0, 10))
        if stored_index != expected_index:
            # Predictor values in readout_grid.json are rounded to three decimals.
            # Only tolerate an index disagreement when the reconstructed value is
            # extremely close to a half-index rounding boundary.
            scaled = probability * 10.0
            distance_to_boundary = min(abs(scaled - (k + 0.5)) for k in range(10))
            if distance_to_boundary > 0.003:
                index_mismatch_count += 1

    _require(
        max_probability_error_pp <= 0.08,
        f"readout reconstructed probability differs by as much as {max_probability_error_pp:.3f} percentage points",
    )
    _require(index_mismatch_count == 0, f"{index_mismatch_count} readout points fail index/probability consistency")

    return {
        "point_count": len(points),
        "max_reconstruction_error_percentage_points": max_probability_error_pp,
    }


def _validate_assets(source, latest: dict) -> None:
    images = latest.get("images") or {}
    image_paths = {
        images.get("index", "assets/latest_index.png"),
        images.get("probability", "assets/latest_probability.png"),
    }
    for layer in (latest.get("layers") or {}).values():
        if layer.get("image"):
            image_paths.add(layer["image"])

    for path in sorted(image_paths):
        data = source.bytes(path)
        _require(data.startswith(PNG_SIGNATURE), f"{path} is not a valid PNG")
        _require(len(data) > 1000, f"{path} is unexpectedly small")

    for key in ("index_contours", "probability_contours"):
        filename = latest.get(key)
        _require(filename, f"latest.json has no {key}")
        payload = source.json(f"data/{filename}")
        _require(payload.get("type") == "FeatureCollection", f"{filename} is not a GeoJSON FeatureCollection")
        _require(isinstance(payload.get("features"), list), f"{filename} has no features array")


def validate(source) -> dict:
    latest = _validate_latest(source)
    readout_summary = _validate_readout(source, latest)
    _validate_assets(source, latest)
    return {
        "cycle": latest.get("cycle"),
        "model": latest.get("model"),
        "readout": readout_summary,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Validate generated or published MBCP guidance artifacts")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--web-dir", type=Path, help="Local web directory containing data/ and assets/")
    group.add_argument("--base-url", help="Published GitHub Pages base URL")
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-seconds", type=float, default=10.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = LocalSource(args.web_dir) if args.web_dir else RemoteSource(args.base_url)

    last_error: Exception | None = None
    for attempt in range(1, max(args.retries, 1) + 1):
        try:
            summary = validate(source)
            print("Guidance artifact validation passed.")
            print(json.dumps(summary, indent=2))
            return 0
        except Exception as exc:
            last_error = exc
            print(f"Validation attempt {attempt}/{max(args.retries, 1)} failed: {exc}")
            if attempt < max(args.retries, 1):
                time.sleep(max(args.retry_seconds, 0.0))

    raise SystemExit(f"Guidance artifact validation failed: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args():
    root = _repo_root()
    parser = argparse.ArgumentParser(
        description="Generate experimental current-hour-valid RAP Gulf Coast guidance"
    )
    parser.add_argument(
        "--grib",
        type=Path,
        help=(
            "Optional local RAP GRIB2 file. If omitted, the preferred RAP product "
            "valid at the current UTC hour is downloaded."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=root / "web" / "data")
    parser.add_argument("--asset-dir", type=Path, default=root / "web" / "assets")
    parser.add_argument("--domain", type=Path, default=root / "config" / "domain.yml")
    parser.add_argument(
        "--model",
        type=Path,
        default=root / "config" / "refined_gulf_coast_model.json",
    )
    parser.add_argument("--cache-dir", type=Path, default=root / "cache")
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help=(
            "Return a non-zero exit code on RAP/model errors. Default writes an "
            "error status for the web page and exits successfully."
        ),
    )
    return parser.parse_args()


def write_error_outputs(output_dir: Path, asset_dir: Path, err: BaseException) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)
    traceback_text = traceback.format_exc()
    (output_dir / "guidance_error.txt").write_text(traceback_text, encoding="utf-8")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "display_version": 3,
        "status": "error",
        "error_message": str(err),
        "error_log": "guidance_error.txt",
        "cycle": {
            "model": "RAP",
            "product": "prs",
            "selection_mode": "current_hour_valid",
            "forecast_hour": 0,
            "cycle_time_utc": "not generated",
            "valid_time_utc": "not generated",
            "source": "RAP guidance generation failed",
        },
        "product": "Experimental Gulf Coast Conditional Damaging Wind Index",
        "index_contours": "index_contours.geojson",
        "probability_contours": "probability_contours.geojson",
        "image": "assets/latest_index.png",
        "images": {
            "index": "assets/latest_index.png",
            "probability": "assets/latest_probability.png",
        },
        "disclaimer": (
            "Experimental/research guidance only. Not official NWS operational guidance."
        ),
    }
    (output_dir / "latest.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    print("RAP guidance generation failed, but wrote web error outputs.")
    print(traceback_text)


def generate_guidance(args) -> None:
    from mbcp_guidance.config import load_json, load_yaml
    from mbcp_guidance.fields import calculate_environmental_fields
    from mbcp_guidance.model import apply_refined_model
    from mbcp_guidance.output import (
        smooth_display_field,
        write_contours,
        write_latest_json,
        write_map_png,
        write_raster_overlay,
    )
    from mbcp_guidance.rap import download_latest_rap, open_grib_datasets

    domain_config = load_yaml(args.domain)
    model_config = load_json(args.model)
    bounding_box = domain_config["bbox"]

    if args.grib:
        grib_path = args.grib
        cycle_meta = {
            "model": "RAP",
            "product": "prs",
            "selection_mode": "local_file",
            "forecast_hour": 0,
            "cycle_time_utc": "unknown-local-file",
            "valid_time_utc": "unknown-local-file",
            "source": str(grib_path),
        }
    else:
        grib_path, cycle_meta = download_latest_rap(args.cache_dir)

    cycle_time = cycle_meta.get("cycle_time_utc", "unknown cycle")
    valid_time = cycle_meta.get("valid_time_utc", cycle_time)
    forecast_hour = int(cycle_meta.get("forecast_hour", 0))
    print(
        f"Selected RAP cycle {cycle_time} f{forecast_hour:02d}, "
        f"valid {valid_time}."
    )
    print(f"Opening RAP file: {grib_path}")
    datasets = open_grib_datasets(grib_path)

    print("Calculating gridded environmental fields...")
    fields = calculate_environmental_fields(datasets, bounding_box)
    print("Applying refined Gulf Coast logistic model...")
    probability, index = apply_refined_model(fields, model_config)

    index_display = smooth_display_field(
        index.clip(min=0, max=10),
        sigma=1.0,
    ).clip(min=0, max=10)
    probability_display = smooth_display_field(
        (probability * 100).clip(min=0, max=100),
        sigma=1.0,
    ).clip(min=0, max=100)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.asset_dir.mkdir(parents=True, exist_ok=True)

    print("Writing smoother compatibility contours...")
    write_contours(
        index_display,
        levels=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        output_path=args.output_dir / "index_contours.geojson",
        name="Experimental 0-10 Index",
        unit="index",
        kind="index",
    )
    write_contours(
        probability_display,
        levels=[5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        output_path=args.output_dir / "probability_contours.geojson",
        name="Conditional Damaging Wind Probability",
        unit="percent",
        kind="probability",
    )

    print("Writing transparent Leaflet raster overlays...")
    index_layer = write_raster_overlay(
        index_display,
        args.asset_dir / "index_overlay.png",
        "index",
    )
    probability_layer = write_raster_overlay(
        probability_display,
        args.asset_dir / "probability_overlay.png",
        "probability",
    )

    print("Writing polished static maps...")
    write_map_png(
        index_display,
        args.asset_dir / "latest_index.png",
        "index",
        cycle_meta,
    )
    write_map_png(
        probability_display,
        args.asset_dir / "latest_probability.png",
        "probability",
        cycle_meta,
    )

    write_latest_json(
        args.output_dir / "latest.json",
        cycle_meta,
        layers={"index": index_layer, "probability": probability_layer},
    )

    error_log = args.output_dir / "guidance_error.txt"
    if error_log.exists():
        error_log.unlink()
    print("Done.")


def main():
    args = parse_args()
    try:
        generate_guidance(args)
    except Exception as err:
        write_error_outputs(args.output_dir, args.asset_dir, err)
        if args.fail_on_error:
            raise


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


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
        "display_version": 6,
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
        "readout": None,
        "field_methods": None,
        "field_stats": None,
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
    from mbcp_guidance.readout import write_readout_grid

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

    mlcape_values = np.asarray(fields["mlcape_jkg"].values, dtype=float)
    mlcape_valid = mlcape_values[np.isfinite(mlcape_values)]
    if mlcape_valid.size == 0:
        raise ValueError("Calculated MLCAPE field contained no valid grid points")
    mlcape_stats = {
        "valid_grid_points": int(mlcape_valid.size),
        "minimum_jkg": round(float(np.min(mlcape_valid)), 1),
        "median_jkg": round(float(np.median(mlcape_valid)), 1),
        "maximum_jkg": round(float(np.max(mlcape_valid)), 1),
    }
    print(
        "Calculated 100-hPa MLCAPE from RAP profiles: "
        f"n={mlcape_stats['valid_grid_points']}, "
        f"min={mlcape_stats['minimum_jkg']:.1f}, "
        f"median={mlcape_stats['median_jkg']:.1f}, "
        f"max={mlcape_stats['maximum_jkg']:.1f} J/kg"
    )

    print("Applying refined Gulf Coast logistic model...")
    probability, index = apply_refined_model(fields, model_config)

    index_raw = index.clip(min=0, max=10)
    probability_raw = (probability * 100).clip(min=0, max=100)
    index_display = smooth_display_field(index_raw, sigma=1.0).clip(min=0, max=10)
    probability_display = smooth_display_field(
        probability_raw,
        sigma=1.0,
    ).clip(min=0, max=100)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.asset_dir.mkdir(parents=True, exist_ok=True)

    print("Writing unsmoothed point-readout and parameter diagnostics data...")
    readout_metadata = write_readout_grid(
        index_raw,
        probability_raw,
        fields,
        model_config,
        args.output_dir / "readout_grid.json",
        cycle_meta,
    )

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

    latest_path = args.output_dir / "latest.json"
    write_latest_json(
        latest_path,
        cycle_meta,
        layers={"index": index_layer, "probability": probability_layer},
    )
    latest_payload = json.loads(latest_path.read_text(encoding="utf-8"))
    latest_payload["display_version"] = 6
    latest_payload["readout"] = readout_metadata
    latest_payload["model"] = {
        "name": model_config.get("name", "refined Gulf Coast model"),
        "version": model_config.get("version", "unknown"),
        "target": model_config.get("target", "conditional damaging wind probability"),
        "predictor_count": len(model_config.get("variables", {})),
    }
    latest_payload["field_methods"] = {
        "mlcape_jkg": {
            "method": "MetPy mixed_layer_cape_cin",
            "mixed_layer_depth_hpa": 100,
            "profile_source": "RAP pressure-level temperature and relative humidity",
            "surface_augmentation": (
                "RAP surface pressure plus 2-m temperature/dewpoint when available; "
                "otherwise lowest valid pressure level"
            ),
            "native_rap_cape_diagnostic_used": False,
        }
    }
    latest_payload["field_stats"] = {"mlcape_jkg": mlcape_stats}
    latest_path.write_text(
        json.dumps(latest_payload, indent=2),
        encoding="utf-8",
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

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
    p = argparse.ArgumentParser(description="Generate experimental RAP-based Gulf Coast MBCP guidance")
    p.add_argument("--grib", type=Path, help="Optional local RAP GRIB2 file. If omitted, latest RAP f00 is downloaded.")
    p.add_argument("--output-dir", type=Path, default=root / "web" / "data")
    p.add_argument("--asset-dir", type=Path, default=root / "web" / "assets")
    p.add_argument("--domain", type=Path, default=root / "config" / "domain.yml")
    p.add_argument("--model", type=Path, default=root / "config" / "refined_gulf_coast_model.json")
    p.add_argument("--cache-dir", type=Path, default=root / "cache")
    p.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return a non-zero exit code on RAP/model errors. Default writes an error status for the web page and exits successfully.",
    )
    return p.parse_args()


def write_error_outputs(output_dir: Path, asset_dir: Path, err: BaseException) -> None:
    """Write a web-readable error status without deleting existing contours/images.

    This keeps GitHub Pages deployable even when the RAP download, GRIB decoding,
    derived-field calculation, or dependency import fails. It also preserves a
    traceback for debugging.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)

    tb = traceback.format_exc()
    (output_dir / "guidance_error.txt").write_text(tb, encoding="utf-8")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "error",
        "error_message": str(err),
        "error_log": "guidance_error.txt",
        "cycle": {
            "model": "RAP",
            "product": "prs",
            "forecast_hour": 0,
            "cycle_time_utc": "not generated",
            "source": "RAP guidance generation failed",
        },
        "product": "Experimental Gulf Coast Conditional Damaging Wind Index",
        "index_contours": "index_contours.geojson",
        "probability_contours": "probability_contours.geojson",
        "disclaimer": "Experimental/research guidance only. Not official NWS operational guidance.",
    }
    (output_dir / "latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("RAP guidance generation failed, but wrote web error outputs.")
    print(tb)


def generate_guidance(args) -> None:
    # Import project/science modules inside the protected block so dependency or
    # GRIB-library startup failures are caught and written to web/data/latest.json.
    from mbcp_guidance.config import load_json, load_yaml
    from mbcp_guidance.fields import calculate_environmental_fields
    from mbcp_guidance.model import apply_refined_model
    from mbcp_guidance.output import write_contours, write_latest_json, write_png
    from mbcp_guidance.rap import download_latest_rap, open_grib_datasets

    domain_cfg = load_yaml(args.domain)
    model_cfg = load_json(args.model)
    bbox = domain_cfg["bbox"]

    if args.grib:
        grib_path = args.grib
        cycle_meta = {
            "model": "RAP",
            "product": "prs",
            "forecast_hour": 0,
            "cycle_time_utc": "unknown-local-file",
            "source": str(grib_path),
        }
    else:
        grib_path, cycle_meta = download_latest_rap(args.cache_dir)

    print(f"Opening RAP file: {grib_path}")
    datasets = open_grib_datasets(grib_path)

    print("Calculating gridded environmental fields...")
    fields = calculate_environmental_fields(datasets, bbox)

    print("Applying refined Gulf Coast logistic model...")
    probability, index = apply_refined_model(fields, model_cfg)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.asset_dir.mkdir(parents=True, exist_ok=True)

    print("Writing web data outputs...")
    write_contours(
        index,
        levels=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        output_path=args.output_dir / "index_contours.geojson",
        name="Experimental 0-10 Index",
        unit="index",
    )
    write_contours(
        probability * 100,
        levels=[0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        output_path=args.output_dir / "probability_contours.geojson",
        name="Damaging Wind Probability",
        unit="percent",
    )
    write_png(
        index,
        output_path=args.asset_dir / "latest_index.png",
        title=f"{domain_cfg['plot']['title']} - {cycle_meta.get('cycle_time_utc', 'unknown cycle')}",
    )
    write_latest_json(args.output_dir / "latest.json", cycle_meta)

    # Remove old error log after a successful run so the web data directory reflects current status.
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

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import numpy as np
import xarray as xr


def _coordinate_names(data: xr.DataArray) -> tuple[str, str]:
    latitude = "latitude" if "latitude" in data.coords else "lat"
    longitude = "longitude" if "longitude" in data.coords else "lon"
    if latitude not in data.coords or longitude not in data.coords:
        raise ValueError("Could not identify latitude/longitude coordinates for readout grid")
    return latitude, longitude


def _coordinate_grids(data: xr.DataArray) -> tuple[np.ndarray, np.ndarray]:
    latitude_name, longitude_name = _coordinate_names(data)
    latitude = np.asarray(data[latitude_name].values, dtype=float)
    longitude = np.asarray(data[longitude_name].values, dtype=float)
    values_shape = np.asarray(data.values).shape

    if latitude.shape == values_shape and longitude.shape == values_shape:
        latitude_grid = latitude
        longitude_grid = longitude
    elif latitude.ndim == 1 and longitude.ndim == 1:
        longitude_grid, latitude_grid = np.meshgrid(longitude, latitude)
    else:
        latitude_grid, longitude_grid = np.broadcast_arrays(latitude, longitude)

    if latitude_grid.shape != values_shape or longitude_grid.shape != values_shape:
        raise ValueError(
            "Latitude/longitude grids do not match the guidance field shape: "
            f"field={values_shape}, latitude={latitude_grid.shape}, "
            f"longitude={longitude_grid.shape}"
        )

    longitude_grid = ((longitude_grid + 180.0) % 360.0) - 180.0
    return latitude_grid, longitude_grid


def _align_field(field: xr.DataArray, template: xr.DataArray, name: str) -> xr.DataArray:
    field = field.squeeze(drop=True).astype(float)
    try:
        aligned, _ = xr.align(field, template, join="exact")
        return aligned
    except (ValueError, IndexError):
        try:
            return field.interp_like(template)
        except Exception as exc:
            raise ValueError(f"Could not align diagnostic field {name!r} to readout grid") from exc


def _model_metadata(model_config: dict) -> dict:
    variables = []
    for key, meta in model_config["variables"].items():
        variables.append(
            {
                "key": key,
                "description": meta.get("description", key),
                "units": meta.get("units", ""),
                "mean": float(meta["mean"]),
                "std": float(meta["std"]),
                "coefficient": float(meta["coefficient"]),
            }
        )

    return {
        "name": model_config.get("name", "refined Gulf Coast model"),
        "version": model_config.get("version", "unknown"),
        "description": model_config.get("description", ""),
        "target": model_config.get("target", "conditional damaging wind probability"),
        "intercept": float(model_config["intercept"]),
        "probability_to_index": model_config.get("probability_to_index", {}),
        "variables": variables,
    }


def write_readout_grid(
    index: xr.DataArray,
    probability_percent: xr.DataArray,
    fields: Mapping[str, xr.DataArray],
    model_config: dict,
    output_path: str | Path,
    cycle_meta: dict,
) -> dict:
    """Write unsmoothed nearest-gridpoint values and model diagnostics.

    The colored map uses light display smoothing, but this file deliberately
    retains the underlying model values. Raw predictor values are included so
    the browser can reconstruct each standardized logistic-model contribution.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    index_data, probability_data = xr.align(
        index.squeeze(drop=True),
        probability_percent.squeeze(drop=True),
        join="exact",
    )
    index_values = np.asarray(index_data.values, dtype=float)
    probability_values = np.asarray(probability_data.values, dtype=float)
    if index_values.ndim != 2 or probability_values.shape != index_values.shape:
        raise ValueError(
            "Readout fields must be matching two-dimensional arrays: "
            f"index={index_values.shape}, probability={probability_values.shape}"
        )

    model_meta = _model_metadata(model_config)
    variable_keys = [variable["key"] for variable in model_meta["variables"]]
    missing = [key for key in variable_keys if key not in fields]
    if missing:
        raise ValueError(
            "Missing model fields required for diagnostic readout: " + ", ".join(missing)
        )

    predictor_arrays: list[np.ndarray] = []
    for key in variable_keys:
        aligned = _align_field(fields[key], index_data, key)
        values = np.asarray(aligned.values, dtype=float)
        if values.shape != index_values.shape:
            raise ValueError(
                f"Diagnostic field {key!r} shape {values.shape} does not match "
                f"readout shape {index_values.shape}"
            )
        predictor_arrays.append(values)

    latitude, longitude = _coordinate_grids(index_data)
    valid = (
        np.isfinite(latitude)
        & np.isfinite(longitude)
        & np.isfinite(index_values)
        & np.isfinite(probability_values)
    )
    for values in predictor_arrays:
        valid &= np.isfinite(values)

    flat_arrays = [values[valid] for values in predictor_arrays]
    points = []
    for point_index, (lat, lon, index_value, probability_value) in enumerate(
        zip(
            latitude[valid],
            longitude[valid],
            index_values[valid],
            probability_values[valid],
        )
    ):
        row = [
            round(float(lat), 4),
            round(float(lon), 4),
            round(float(index_value), 2),
            round(float(probability_value), 1),
        ]
        row.extend(round(float(values[point_index]), 3) for values in flat_arrays)
        points.append(row)

    columns = [
        "latitude",
        "longitude",
        "index",
        "probability_percent",
        *variable_keys,
    ]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycle": cycle_meta,
        "columns": columns,
        "model": model_meta,
        "sampling": "nearest RAP grid point",
        "values": "unsmoothed model output",
        "diagnostics": (
            "For each predictor: z=(value-training_mean)/training_std and "
            "logit contribution=coefficient*z. Positive contributions raise "
            "the modeled conditional probability; negative contributions lower it."
        ),
        "max_distance_km": 40,
        "points": points,
    }
    output_path.write_text(
        json.dumps(payload, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )

    return {
        "file": output_path.name,
        "sampling": payload["sampling"],
        "values": payload["values"],
        "diagnostics": True,
        "max_distance_km": payload["max_distance_km"],
        "point_count": len(points),
        "model_name": model_meta["name"],
        "model_version": model_meta["version"],
        "predictor_count": len(variable_keys),
    }

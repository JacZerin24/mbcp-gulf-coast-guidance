from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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


def write_readout_grid(
    index: xr.DataArray,
    probability_percent: xr.DataArray,
    output_path: str | Path,
    cycle_meta: dict,
) -> dict:
    """Write compact unsmoothed nearest-gridpoint values for the web map.

    The colored map uses light display smoothing, but this readout deliberately
    retains the underlying model's unsmoothed grid values.
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

    latitude, longitude = _coordinate_grids(index_data)
    valid = (
        np.isfinite(latitude)
        & np.isfinite(longitude)
        & np.isfinite(index_values)
        & np.isfinite(probability_values)
    )

    points = [
        [
            round(float(lat), 4),
            round(float(lon), 4),
            round(float(index_value), 2),
            round(float(probability_value), 1),
        ]
        for lat, lon, index_value, probability_value in zip(
            latitude[valid],
            longitude[valid],
            index_values[valid],
            probability_values[valid],
        )
    ]

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycle": cycle_meta,
        "columns": [
            "latitude",
            "longitude",
            "index",
            "probability_percent",
        ],
        "sampling": "nearest RAP grid point",
        "values": "unsmoothed model output",
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
        "max_distance_km": payload["max_distance_km"],
        "point_count": len(points),
    }

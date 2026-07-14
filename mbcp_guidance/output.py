from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import geojsoncontour
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr


def _lat_lon(data: xr.DataArray):
    lat_name = "latitude" if "latitude" in data.coords else "lat"
    lon_name = "longitude" if "longitude" in data.coords else "lon"
    return data[lat_name].values, data[lon_name].values


def write_contours(data: xr.DataArray, levels: list[float], output_path: str | Path, name: str, unit: str):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lat, lon = _lat_lon(data)
    fig, ax = plt.subplots(figsize=(8, 6))
    cs = ax.contourf(lon, lat, data.values, levels=levels)
    geojson = geojsoncontour.contourf_to_geojson(
        contourf=cs,
        ndigits=3,
        unit=unit,
        stroke_width=1,
        fill_opacity=0.55,
        min_angle_deg=3.0,
    )
    plt.close(fig)

    payload = json.loads(geojson)
    payload["properties"] = {"name": name, "unit": unit}
    output_path.write_text(json.dumps(payload), encoding="utf-8")


def write_png(index: xr.DataArray, output_path: str | Path, title: str):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lat, lon = _lat_lon(index)

    fig = plt.figure(figsize=(10, 8))
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature

        ax = plt.axes(projection=ccrs.PlateCarree())
        ax.set_extent([np.nanmin(lon), np.nanmax(lon), np.nanmin(lat), np.nanmax(lat)])
        ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, linewidth=0.5)
        ax.add_feature(cfeature.STATES, linewidth=0.6)
        ax.add_feature(cfeature.LAND, alpha=0.15)
        ax.add_feature(cfeature.OCEAN, alpha=0.15)
        transform = ccrs.PlateCarree()
    except Exception:
        ax = plt.axes()
        transform = None

    levels = np.arange(0, 11, 1)
    kwargs = {"levels": levels}
    if transform is not None:
        kwargs["transform"] = transform
    cf = ax.contourf(lon, lat, index.values, **kwargs)
    cbar = plt.colorbar(cf, ax=ax, shrink=0.75)
    cbar.set_label("Experimental 0-10 Index")
    ax.set_title(title)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def write_latest_json(
    output_path: str | Path,
    cycle_meta: dict,
    index_file: str = "index_contours.geojson",
    probability_file: str = "probability_contours.geojson",
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycle": cycle_meta,
        "product": "Experimental Gulf Coast Conditional Damaging Wind Index",
        "index_contours": index_file,
        "probability_contours": probability_file,
        "disclaimer": "Experimental/research guidance only. Not official NWS operational guidance.",
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

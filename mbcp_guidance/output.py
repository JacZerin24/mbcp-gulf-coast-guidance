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
from matplotlib.colors import LinearSegmentedColormap, Normalize
from scipy.ndimage import gaussian_filter


INDEX_COLORS = [
    "#d9f0ff",
    "#9bd7f5",
    "#58b8e8",
    "#2f9e89",
    "#67b94b",
    "#d9cf38",
    "#f3a43b",
    "#e85d3f",
    "#9e2f62",
]
PROBABILITY_COLORS = [
    "#fff7bc",
    "#fee391",
    "#fec44f",
    "#fe9929",
    "#ec7014",
    "#cc4c02",
    "#993404",
    "#7f2704",
    "#5a1830",
    "#35103d",
]


FIELD_STYLES = {
    "index": {
        "title": "Experimental Gulf Coast Damaging Wind Index",
        "label": "Conditional damaging-wind favorability index",
        "units": "index",
        "threshold": 1.0,
        "vmin": 1.0,
        "vmax": 10.0,
        "ticks": list(range(1, 11)),
        "colors": INDEX_COLORS,
        "legend_edges": list(range(1, 11)),
    },
    "probability": {
        "title": "Experimental Gulf Coast Damaging Wind Probability",
        "label": "Conditional damaging-wind probability (%)",
        "units": "percent",
        "threshold": 5.0,
        "vmin": 5.0,
        "vmax": 100.0,
        "ticks": [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        "colors": PROBABILITY_COLORS,
        "legend_edges": [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    },
}


def _lat_lon(data: xr.DataArray):
    lat_name = "latitude" if "latitude" in data.coords else "lat"
    lon_name = "longitude" if "longitude" in data.coords else "lon"
    return data[lat_name].values, data[lon_name].values


def _style(kind: str) -> dict:
    if kind not in FIELD_STYLES:
        raise ValueError(f"Unknown display field kind: {kind}")
    return FIELD_STYLES[kind]


def smooth_display_field(data: xr.DataArray, sigma: float = 1.0) -> xr.DataArray:
    """Apply light, NaN-aware Gaussian smoothing for visualization only.

    The model values are not changed before the probability/index calculation;
    this function is used only for rendered contours and images.
    """
    squeezed = data.squeeze(drop=True)
    values = np.asarray(squeezed.values, dtype=float)
    if values.ndim != 2:
        raise ValueError(f"Expected a 2-D display field, got shape {values.shape}")

    valid = np.isfinite(values)
    if not np.any(valid):
        return squeezed.copy(deep=True)

    filled = np.where(valid, values, 0.0)
    weights = gaussian_filter(valid.astype(float), sigma=sigma, mode="nearest")
    filtered = gaussian_filter(filled, sigma=sigma, mode="nearest")
    smoothed = np.divide(
        filtered,
        weights,
        out=np.full_like(filtered, np.nan, dtype=float),
        where=weights > 1.0e-6,
    )
    smoothed[~valid] = np.nan

    return xr.DataArray(
        smoothed,
        coords=squeezed.coords,
        dims=squeezed.dims,
        attrs=squeezed.attrs,
        name=squeezed.name,
    )


def data_bounds(data: xr.DataArray) -> list[list[float]]:
    lat, lon = _lat_lon(data)
    return [
        [float(np.nanmin(lat)), float(np.nanmin(lon))],
        [float(np.nanmax(lat)), float(np.nanmax(lon))],
    ]


def _cmap_norm(kind: str):
    style = _style(kind)
    cmap = LinearSegmentedColormap.from_list(f"mbcp_{kind}", style["colors"], N=256)
    cmap.set_bad((0, 0, 0, 0))
    norm = Normalize(vmin=style["vmin"], vmax=style["vmax"], clip=True)
    return cmap, norm


def _masked_values(data: xr.DataArray, kind: str) -> np.ma.MaskedArray:
    style = _style(kind)
    values = np.asarray(data.values, dtype=float)
    return np.ma.masked_where(~np.isfinite(values) | (values < style["threshold"]), values)


def _legend_entries(kind: str) -> list[dict]:
    style = _style(kind)
    edges = style["legend_edges"]
    cmap, norm = _cmap_norm(kind)
    entries = []
    for lower, upper in zip(edges[:-1], edges[1:]):
        midpoint = (lower + upper) / 2
        rgba = cmap(norm(midpoint))
        color = matplotlib.colors.to_hex(rgba, keep_alpha=False)
        entries.append(
            {
                "min": lower,
                "max": upper,
                "label": f"{lower:g}–{upper:g}",
                "color": color,
            }
        )
    return entries


def write_contours(
    data: xr.DataArray,
    levels: list[float],
    output_path: str | Path,
    name: str,
    unit: str,
    kind: str,
):
    """Write simplified compatibility contours.

    The live web map uses transparent raster overlays for a smoother display, but
    these contours remain available for downloads and future vector uses.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lat, lon = _lat_lon(data)
    values = _masked_values(data, kind)
    cmap, norm = _cmap_norm(kind)

    fig, ax = plt.subplots(figsize=(8, 6))
    cs = ax.contourf(lon, lat, values, levels=levels, cmap=cmap, norm=norm, antialiased=True)
    geojson = geojsoncontour.contourf_to_geojson(
        contourf=cs,
        ndigits=3,
        unit=unit,
        stroke_width=0.45,
        fill_opacity=0.38,
        min_angle_deg=5.0,
    )
    plt.close(fig)

    payload = json.loads(geojson)
    payload["properties"] = {
        "name": name,
        "unit": unit,
        "display_smoothing": "Gaussian sigma 1.0 grid cells; visualization only",
    }
    output_path.write_text(json.dumps(payload), encoding="utf-8")


def _mercator_y(latitude_degrees):
    latitude = np.clip(np.asarray(latitude_degrees, dtype=float), -85.0, 85.0)
    radians = np.deg2rad(latitude)
    return np.log(np.tan((np.pi / 4.0) + (radians / 2.0)))


def write_raster_overlay(data: xr.DataArray, output_path: str | Path, kind: str) -> dict:
    """Write a transparent Web-Mercator-aligned raster for Leaflet."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lat, lon = _lat_lon(data)
    y = _mercator_y(lat)
    raw_values = np.asarray(data.values, dtype=float)
    values = np.ma.masked_invalid(raw_values)
    cmap, norm = _cmap_norm(kind)
    style = _style(kind)
    fade_width = 0.75 if kind == "index" else 5.0
    alpha = np.where(
        np.isfinite(raw_values),
        np.clip((raw_values - style["threshold"]) / fade_width, 0.0, 1.0),
        0.0,
    )

    west, east = float(np.nanmin(lon)), float(np.nanmax(lon))
    south, north = float(np.nanmin(lat)), float(np.nanmax(lat))
    y_south, y_north = float(np.nanmin(y)), float(np.nanmax(y))

    projected_ratio = max((east - west) / max(y_north - y_south, 1.0e-6), 0.5)
    width_px = 1400
    height_px = int(np.clip(width_px / projected_ratio, 650, 1300))
    dpi = 100

    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, frameon=False)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(west, east)
    ax.set_ylim(y_south, y_north)
    ax.axis("off")
    ax.pcolormesh(
        lon,
        y,
        values,
        shading="gouraud",
        cmap=cmap,
        norm=norm,
        alpha=alpha,
        antialiased=True,
        rasterized=True,
    )
    fig.savefig(output_path, dpi=dpi, transparent=True, pad_inches=0)
    plt.close(fig)

    return {
        "image": f"assets/{output_path.name}",
        "bounds": [[south, west], [north, east]],
        "opacity": 0.74,
        "label": style["label"],
        "units": style["units"],
        "threshold": style["threshold"],
        "legend": _legend_entries(kind),
        "display_smoothing": "Gaussian sigma 1.0 grid cells; visualization only",
    }


def write_map_png(
    data: xr.DataArray,
    output_path: str | Path,
    kind: str,
    cycle_time_utc: str,
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    style = _style(kind)
    lat, lon = _lat_lon(data)
    values = _masked_values(data, kind)
    cmap, norm = _cmap_norm(kind)

    fig = plt.figure(figsize=(12, 8), facecolor="white")
    transform = None
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature

        ax = plt.axes(projection=ccrs.PlateCarree())
        transform = ccrs.PlateCarree()
        ax.set_extent(
            [float(np.nanmin(lon)), float(np.nanmax(lon)), float(np.nanmin(lat)), float(np.nanmax(lat))],
            crs=transform,
        )
        ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#dceef5", zorder=0)
        ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#f5f4ef", zorder=0)
        ax.add_feature(cfeature.LAKES.with_scale("50m"), facecolor="#dceef5", edgecolor="#9bbdcc", linewidth=0.4)
        ax.add_feature(cfeature.COASTLINE.with_scale("50m"), edgecolor="#374151", linewidth=0.75, zorder=5)
        ax.add_feature(cfeature.STATES.with_scale("50m"), edgecolor="#4b5563", linewidth=0.65, zorder=5)
        ax.add_feature(cfeature.BORDERS.with_scale("50m"), edgecolor="#6b7280", linewidth=0.5, zorder=5)
        gridlines = ax.gridlines(
            draw_labels=True,
            linewidth=0.35,
            color="#6b7280",
            alpha=0.35,
            linestyle="--",
            x_inline=False,
            y_inline=False,
        )
        gridlines.top_labels = False
        gridlines.right_labels = False
        gridlines.xlabel_style = {"size": 8, "color": "#4b5563"}
        gridlines.ylabel_style = {"size": 8, "color": "#4b5563"}
    except Exception:
        ax = plt.axes()
        ax.set_facecolor("#f5f4ef")
        ax.set_xlim(float(np.nanmin(lon)), float(np.nanmax(lon)))
        ax.set_ylim(float(np.nanmin(lat)), float(np.nanmax(lat)))
        ax.grid(True, linestyle="--", linewidth=0.35, alpha=0.35)
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")

    plot_levels = np.linspace(style["vmin"], style["vmax"], 46)
    kwargs = {
        "levels": plot_levels,
        "cmap": cmap,
        "norm": norm,
        "extend": "max",
        "antialiased": True,
        "zorder": 2,
    }
    if transform is not None:
        kwargs["transform"] = transform

    cf = ax.contourf(lon, lat, values, **kwargs)

    line_levels = style["ticks"][1:-1]
    line_kwargs = {
        "levels": line_levels,
        "colors": "#263238",
        "linewidths": 0.35,
        "alpha": 0.35,
        "zorder": 3,
    }
    if transform is not None:
        line_kwargs["transform"] = transform
    ax.contour(lon, lat, values, **line_kwargs)

    fig.suptitle(style["title"], fontsize=19, fontweight="semibold", y=0.965, color="#111827")
    ax.set_title(
        f"RAP f00 cycle: {cycle_time_utc}  |  Conditional environmental guidance",
        fontsize=11,
        color="#4b5563",
        pad=10,
    )

    cbar = fig.colorbar(cf, ax=ax, orientation="horizontal", pad=0.065, shrink=0.84, aspect=38)
    cbar.set_label(style["label"], fontsize=11)
    cbar.set_ticks(style["ticks"])
    cbar.ax.tick_params(labelsize=9)

    fig.text(
        0.5,
        0.018,
        "Light display smoothing is applied for visualization only. Experimental/research guidance; not official NWS guidance.",
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#6b7280",
    )
    fig.subplots_adjust(left=0.055, right=0.975, top=0.88, bottom=0.13)
    fig.savefig(output_path, dpi=180, facecolor="white", bbox_inches="tight")
    plt.close(fig)


def write_latest_json(
    output_path: str | Path,
    cycle_meta: dict,
    layers: dict,
    index_file: str = "index_contours.geojson",
    probability_file: str = "probability_contours.geojson",
    index_image: str = "assets/latest_index.png",
    probability_image: str = "assets/latest_probability.png",
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "display_version": 2,
        "cycle": cycle_meta,
        "product": "Experimental Gulf Coast Conditional Damaging Wind Index",
        "index_contours": index_file,
        "probability_contours": probability_file,
        "image": index_image,
        "images": {
            "index": index_image,
            "probability": probability_image,
        },
        "layers": layers,
        "display_note": "Light Gaussian smoothing is applied to rendered products only; model calculations are unchanged.",
        "disclaimer": "Experimental/research guidance only. Not official NWS operational guidance.",
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

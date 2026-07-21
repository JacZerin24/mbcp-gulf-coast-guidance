from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import cfgrib
import xarray as xr


RAP_PRESSURE_PRODUCT = "awp130pgrb"


def latest_rap_f00(max_lookback_hours: int = 12, cache_dir: str | Path = "cache"):
    """Return the newest available 0-hr 13-km RAP pressure-level analysis."""
    from herbie import Herbie

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    errors: list[str] = []

    for hours_back in range(max_lookback_hours + 1):
        dt = now - timedelta(hours=hours_back)
        try:
            h = Herbie(
                dt,
                model="rap",
                product=RAP_PRESSURE_PRODUCT,
                fxx=0,
                save_dir=cache_dir,
                verbose=False,
            )
            inv = h.inventory()
            if inv is not None and len(inv) > 0:
                return h
            errors.append(f"{dt:%Y-%m-%d %HZ}: inventory was empty")
        except Exception as exc:
            errors.append(f"{dt:%Y-%m-%d %HZ}: {type(exc).__name__}: {exc}")

    detail = errors[-1] if errors else "no additional error information"
    raise RuntimeError(
        f"No RAP f00 {RAP_PRESSURE_PRODUCT} cycle found in the past "
        f"{max_lookback_hours} hours. Last attempt: {detail}"
    )


def download_latest_rap(cache_dir: str | Path = "cache") -> tuple[Path, dict]:
    """Download the latest available RAP f00 13-km pressure-level file."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    h = latest_rap_f00(cache_dir=cache_dir)
    path = Path(h.download(save_dir=cache_dir, errors="raise"))
    meta = {
        "model": "RAP",
        "product": RAP_PRESSURE_PRODUCT,
        "forecast_hour": 0,
        "cycle_time_utc": h.date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(getattr(h, "grib_source", getattr(h, "grib", "Herbie"))),
    }
    return path, meta


def open_grib_datasets(path: str | Path) -> list[xr.Dataset]:
    """Open all cfgrib-compatible groups from a RAP GRIB2 file."""
    return cfgrib.open_datasets(str(path), backend_kwargs={"indexpath": ""})


def standardize_lon(lon):
    """Convert longitudes from 0-360 to -180 to 180 if needed."""
    return ((lon + 180) % 360) - 180


def latlon_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_name = "latitude" if "latitude" in ds.coords else "lat"
    lon_name = "longitude" if "longitude" in ds.coords else "lon"
    if lat_name not in ds.coords or lon_name not in ds.coords:
        raise ValueError("Could not identify latitude/longitude coordinates in dataset")
    return lat_name, lon_name


def subset_domain(ds: xr.Dataset | xr.DataArray, bbox: dict):
    """Subset a RAP xarray object to a lat/lon bounding box.

    Works with 2D latitude/longitude grids by masking outside the box.
    """
    lat_name, lon_name = latlon_names(ds)
    lon = standardize_lon(ds[lon_name])
    lat = ds[lat_name]
    mask = (
        (lat >= bbox["south"])
        & (lat <= bbox["north"])
        & (lon >= bbox["west"])
        & (lon <= bbox["east"])
    )
    return ds.where(mask, drop=True)


def find_isobaric_dataset(datasets: list[xr.Dataset]) -> xr.Dataset:
    """Find a dataset containing pressure-level temperature fields."""
    for ds in datasets:
        if "isobaricInhPa" in ds.coords and any(v in ds.data_vars for v in ["t", "gh", "r"]):
            return ds
    raise ValueError("Could not find an isobaric pressure-level RAP dataset")


def find_field(datasets: list[xr.Dataset], candidates: list[str]) -> xr.DataArray | None:
    """Find a field by data variable name or GRIB shortName.

    This is intentionally forgiving because RAP GRIB short names can vary across products.
    """
    cand_lower = {c.lower() for c in candidates}
    for ds in datasets:
        for var_name, da in ds.data_vars.items():
            attrs = da.attrs
            names = {
                var_name.lower(),
                str(attrs.get("GRIB_shortName", "")).lower(),
                str(attrs.get("GRIB_name", "")).lower(),
                str(attrs.get("long_name", "")).lower(),
                str(attrs.get("standard_name", "")).lower(),
            }
            if names & cand_lower:
                return da.squeeze(drop=True)
    return None

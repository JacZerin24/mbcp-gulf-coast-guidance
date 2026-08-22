from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import cfgrib
import xarray as xr


RAP_PRESSURE_PRODUCT = "awp130pgrb"


def current_valid_hour_utc(now: datetime | None = None) -> datetime:
    """Return the current UTC hour as a timezone-naive datetime for Herbie."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    return now.replace(
        tzinfo=None,
        minute=0,
        second=0,
        microsecond=0,
    )


def latest_rap_f00(max_lookback_hours: int = 12, cache_dir: str | Path = "cache"):
    """Return the newest available 0-hour 13-km RAP pressure-level analysis."""
    from herbie import Herbie

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    now = current_valid_hour_utc()
    errors: list[str] = []

    for hours_back in range(max_lookback_hours + 1):
        cycle_dt = now - timedelta(hours=hours_back)
        try:
            herbie = Herbie(
                cycle_dt,
                model="rap",
                product=RAP_PRESSURE_PRODUCT,
                fxx=0,
                save_dir=cache_dir,
                verbose=False,
            )
            inventory = herbie.inventory()
            if inventory is not None and len(inventory) > 0:
                return herbie
            errors.append(f"{cycle_dt:%Y-%m-%d %HZ} f00: inventory was empty")
        except Exception as exc:
            errors.append(
                f"{cycle_dt:%Y-%m-%d %HZ} f00: {type(exc).__name__}: {exc}"
            )

    detail = errors[-1] if errors else "no additional error information"
    raise RuntimeError(
        f"No RAP f00 {RAP_PRESSURE_PRODUCT} cycle found in the past "
        f"{max_lookback_hours} hours. Last attempt: {detail}"
    )


def latest_rap_valid_now(
    max_lookback_hours: int = 12,
    cache_dir: str | Path = "cache",
    valid_hour: datetime | None = None,
):
    """Return the preferred RAP product valid at the requested UTC hour.

    Preference is given to the newest possible cycle. For the current 14Z valid
    hour, the search order is 14Z f00, 13Z f01, 12Z f02, and so on. This keeps
    the guidance valid for the current hour even when the newest analysis has
    not arrived yet.
    """
    from herbie import Herbie

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    valid_dt = current_valid_hour_utc(valid_hour)
    errors: list[str] = []

    for forecast_hour in range(max_lookback_hours + 1):
        cycle_dt = valid_dt - timedelta(hours=forecast_hour)
        try:
            herbie = Herbie(
                cycle_dt,
                model="rap",
                product=RAP_PRESSURE_PRODUCT,
                fxx=forecast_hour,
                save_dir=cache_dir,
                verbose=False,
            )
            inventory = herbie.inventory()
            if inventory is not None and len(inventory) > 0:
                return herbie, valid_dt
            errors.append(
                f"cycle {cycle_dt:%Y-%m-%d %HZ} f{forecast_hour:02d}: "
                "inventory was empty"
            )
        except Exception as exc:
            errors.append(
                f"cycle {cycle_dt:%Y-%m-%d %HZ} f{forecast_hour:02d}: "
                f"{type(exc).__name__}: {exc}"
            )

    detail = errors[-1] if errors else "no additional error information"
    raise RuntimeError(
        f"No RAP {RAP_PRESSURE_PRODUCT} product valid at "
        f"{valid_dt:%Y-%m-%d %HZ} was found using forecast hours 0 through "
        f"{max_lookback_hours}. Last attempt: {detail}"
    )


def download_latest_rap(cache_dir: str | Path = "cache") -> tuple[Path, dict]:
    """Download the preferred RAP pressure-level file valid this UTC hour."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    herbie, valid_dt = latest_rap_valid_now(cache_dir=cache_dir)
    path = Path(herbie.download(save_dir=cache_dir, errors="raise"))
    forecast_hour = int(getattr(herbie, "fxx", 0))
    source = getattr(herbie, "grib_source", None) or getattr(
        herbie, "grib", "Herbie"
    )

    meta = {
        "model": "RAP",
        "product": RAP_PRESSURE_PRODUCT,
        "selection_mode": "current_hour_valid",
        "forecast_hour": forecast_hour,
        "cycle_time_utc": herbie.date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "valid_time_utc": valid_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(source),
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
    """Find the complete RAP isobaric thermodynamic dataset.

    cfgrib can return multiple pressure-level groups. Prefer a group containing
    temperature, relative humidity, and geopotential height together instead of
    accepting the first group that happens to contain only one relevant field.
    """
    candidates = [ds for ds in datasets if "isobaricInhPa" in ds.coords]

    for ds in candidates:
        has_temperature = "t" in ds.data_vars
        has_rh = "r" in ds.data_vars
        has_height = "gh" in ds.data_vars or "z" in ds.data_vars
        if has_temperature and has_rh and has_height:
            return ds

    # If cfgrib split compatible isobaric fields into separate datasets, try to
    # merge them by their shared pressure/grid coordinates before giving up.
    if candidates:
        try:
            merged = xr.merge(candidates, compat="override", join="inner")
            if (
                "t" in merged.data_vars
                and "r" in merged.data_vars
                and ("gh" in merged.data_vars or "z" in merged.data_vars)
            ):
                return merged
        except Exception:
            pass

    raise ValueError(
        "Could not find a complete isobaric RAP dataset containing temperature, "
        "relative humidity, and geopotential height"
    )


def find_field(datasets: list[xr.Dataset], candidates: list[str]) -> xr.DataArray | None:
    """Find a field by data variable name or GRIB shortName.

    This is intentionally forgiving because RAP GRIB short names can vary across products.
    """
    candidate_names = {candidate.lower() for candidate in candidates}
    for ds in datasets:
        for variable_name, data_array in ds.data_vars.items():
            attrs = data_array.attrs
            names = {
                variable_name.lower(),
                str(attrs.get("GRIB_shortName", "")).lower(),
                str(attrs.get("GRIB_name", "")).lower(),
                str(attrs.get("long_name", "")).lower(),
                str(attrs.get("standard_name", "")).lower(),
            }
            if names & candidate_names:
                return data_array.squeeze(drop=True)
    return None

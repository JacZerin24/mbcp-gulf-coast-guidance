from __future__ import annotations

import numpy as np
import xarray as xr
from metpy.calc import dewpoint_from_relative_humidity, equivalent_potential_temperature
from metpy.units import units

from .rap import find_field, find_isobaric_dataset, standardize_lon, subset_domain


def _pressure_coord(ds: xr.Dataset) -> str:
    if "isobaricInhPa" not in ds.coords:
        raise ValueError("Dataset does not contain isobaricInhPa")
    return "isobaricInhPa"


def _temp_c(ds: xr.Dataset, level_hpa: int) -> xr.DataArray:
    lev = _pressure_coord(ds)
    t = ds["t"].sel({lev: level_hpa}, method="nearest")
    if float(t.max()) > 100:
        t = t - 273.15
    return t.squeeze(drop=True)


def _height_m(ds: xr.Dataset, level_hpa: int) -> xr.DataArray:
    lev = _pressure_coord(ds)
    if "gh" in ds.data_vars:
        z = ds["gh"].sel({lev: level_hpa}, method="nearest")
    elif "z" in ds.data_vars:
        # Some GRIB decoders store geopotential instead of geopotential height.
        z = ds["z"].sel({lev: level_hpa}, method="nearest") / 9.80665
    else:
        raise ValueError("No height/geopotential field found for lapse-rate calculations")
    return z.squeeze(drop=True)


def _find_3km_level(ds: xr.Dataset) -> int:
    """Return the pressure level whose mean height is closest to 3 km MSL.

    The domain is near sea level, so MSL is an acceptable first prototype approximation.
    """
    lev = _pressure_coord(ds)
    levels = [int(x) for x in ds[lev].values if 400 <= int(x) <= 1000]
    candidates = []
    for level in levels:
        try:
            zmean = float(_height_m(ds, level).mean(skipna=True))
            candidates.append((abs(zmean - 3000.0), level))
        except Exception:
            continue
    if not candidates:
        return 700
    return sorted(candidates)[0][1]


def _lapse_rate_between(ds: xr.Dataset, bottom_hpa: int, top_hpa: int) -> xr.DataArray:
    tb = _temp_c(ds, bottom_hpa)
    tt = _temp_c(ds, top_hpa)
    zb = _height_m(ds, bottom_hpa)
    zt = _height_m(ds, top_hpa)
    dz_km = (zt - zb) / 1000.0
    return ((tb - tt) / dz_km).where(dz_km > 0)


def _thetae_deficit(ds: xr.Dataset) -> xr.DataArray:
    """Prototype theta-e deficit from low levels to mid levels.

    Calculation:
    max theta-e from 1000-850 mb minus min theta-e from 700-500 mb.
    This may not exactly match the research spreadsheet extraction and should be validated.
    """
    lev = _pressure_coord(ds)
    if "r" not in ds.data_vars:
        raise ValueError("Relative humidity field 'r' is required for theta-e deficit")

    pressure = ds[lev] * units.hPa
    temp = ds["t"]
    if float(temp.max()) < 150:
        temp_k = (temp + 273.15) * units.kelvin
    else:
        temp_k = temp * units.kelvin
    rh = (ds["r"].clip(1, 100) / 100.0).values

    dewpoint = dewpoint_from_relative_humidity(temp_k, rh)
    thetae = equivalent_potential_temperature(pressure, temp_k, dewpoint)
    thetae_da = xr.DataArray(
        np.asarray(thetae.magnitude),
        dims=ds["t"].dims,
        coords=ds["t"].coords,
        name="thetae_k",
    )

    low = thetae_da.sel({lev: [p for p in thetae_da[lev].values if 850 <= p <= 1000]}).max(lev)
    mid = thetae_da.sel({lev: [p for p in thetae_da[lev].values if 500 <= p <= 700]}).min(lev)
    return (low - mid).rename("thetae_deficit_k")


def _diagnostic_or_nan(
    datasets: list[xr.Dataset],
    bbox: dict,
    candidates: list[str],
    template: xr.DataArray,
    name: str,
) -> xr.DataArray:
    da = find_field(datasets, candidates)
    if da is None:
        out = xr.full_like(template, np.nan, dtype=float)
        out.name = name
        return out
    da = subset_domain(da, bbox)
    da = da.squeeze(drop=True).astype(float)
    # Try to align to the template grid. If coordinates are equivalent this is a no-op.
    try:
        da = da.interp_like(template)
    except Exception:
        pass
    da.name = name
    return da


def calculate_environmental_fields(datasets: list[xr.Dataset], bbox: dict) -> dict[str, xr.DataArray]:
    """Calculate gridded fields required by the refined model.

    This is the full-gridded prototype. It calculates the simple pressure-level terms directly
    from the RAP pressure-level grid and uses RAP diagnostic fields for CAPE/LI/DCAPE when those
    are available in the decoded GRIB groups.
    """
    iso = subset_domain(find_isobaric_dataset(datasets), bbox)

    fields: dict[str, xr.DataArray] = {}
    fields["vertical_totals_850_500_c"] = (_temp_c(iso, 850) - _temp_c(iso, 500)).rename(
        "vertical_totals_850_500_c"
    )
    fields["mid_level_lapse_rate_c_km"] = _lapse_rate_between(iso, 700, 500).rename(
        "mid_level_lapse_rate_c_km"
    )

    three_km_level = _find_3km_level(iso)
    fields["sfc_3km_lapse_rate_c_km"] = _lapse_rate_between(iso, 1000, three_km_level).rename(
        "sfc_3km_lapse_rate_c_km"
    )

    fields["thetae_deficit_k"] = _thetae_deficit(iso)

    template = fields["vertical_totals_850_500_c"]
    fields["mlcape_jkg"] = _diagnostic_or_nan(
        datasets,
        bbox,
        candidates=["mlcape", "mixed layer cape", "cape"],
        template=template,
        name="mlcape_jkg",
    )
    fields["sbli_c"] = _diagnostic_or_nan(
        datasets,
        bbox,
        candidates=["lftx", "4lftx", "lifted index", "surface lifted index"],
        template=template,
        name="sbli_c",
    )
    fields["dcape_jkg"] = _diagnostic_or_nan(
        datasets,
        bbox,
        candidates=["dcape", "downdraft cape"],
        template=template,
        name="dcape_jkg",
    )

    lat_name = "latitude" if "latitude" in template.coords else "lat"
    lon_name = "longitude" if "longitude" in template.coords else "lon"
    for key, da in list(fields.items()):
        if lon_name in da.coords:
            da = da.assign_coords({lon_name: standardize_lon(da[lon_name])})
        fields[key] = da

    missing = [k for k, v in fields.items() if bool(v.isnull().all())]
    if missing:
        raise ValueError(
            "These required fields were not available/calculable from the RAP file: "
            + ", ".join(missing)
            + ". Check GRIB inventory/search patterns or add a custom calculation."
        )

    return fields

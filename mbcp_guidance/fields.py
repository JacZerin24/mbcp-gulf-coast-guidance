from __future__ import annotations

import numpy as np
import xarray as xr
from metpy.calc import (
    dewpoint_from_relative_humidity,
    downdraft_cape,
    equivalent_potential_temperature,
    mixed_layer_cape_cin,
)
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

    # MetPy can return either a pint Quantity or an xarray DataArray backed by
    # a pint Quantity. Preserve xarray dimensions/coordinates when available.
    if isinstance(thetae, xr.DataArray):
        try:
            thetae_da = thetae.metpy.dequantify().astype(float)
        except (AttributeError, TypeError, ValueError):
            data = getattr(thetae.data, "magnitude", thetae.data)
            thetae_da = xr.DataArray(
                np.asarray(data, dtype=float),
                dims=thetae.dims,
                coords=thetae.coords,
            )
        thetae_da = thetae_da.rename("thetae_k")
    else:
        data = getattr(thetae, "magnitude", thetae)
        thetae_da = xr.DataArray(
            np.asarray(data, dtype=float),
            dims=ds["t"].dims,
            coords=ds["t"].coords,
            name="thetae_k",
        )

    low_levels = [p for p in thetae_da[lev].values if 850 <= p <= 1000]
    mid_levels = [p for p in thetae_da[lev].values if 500 <= p <= 700]
    if not low_levels or not mid_levels:
        raise ValueError("RAP pressure levels needed for theta-e deficit were not available")

    low = thetae_da.sel({lev: low_levels}).max(lev)
    mid = thetae_da.sel({lev: mid_levels}).min(lev)
    return (low - mid).rename("thetae_deficit_k")


def _to_kelvin(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if np.nanmax(values) < 150:
        return values + 273.15
    return values


def _to_hpa(value: float) -> float:
    value = float(value)
    return value / 100.0 if value > 2000 else value


def _mlcape_profile_value(
    temperature_profile: np.ndarray,
    rh_profile: np.ndarray,
    surface_pressure: float,
    surface_temperature: float,
    surface_dewpoint: float,
    pressure_hpa: np.ndarray,
) -> float:
    """Calculate 100-hPa mixed-layer CAPE for one RAP gridpoint.

    The parcel is calculated directly from RAP temperature/moisture profiles using
    MetPy's ``mixed_layer_cape_cin``. When RAP surface pressure plus 2-m temperature
    and dewpoint are available, that surface observation is prepended to the
    pressure-level profile so the mixed layer begins at the analyzed surface.
    Otherwise the lowest valid pressure level is used as the profile bottom.
    """
    temperature = np.asarray(temperature_profile, dtype=float)
    relative_humidity = np.asarray(rh_profile, dtype=float)
    pressure = np.asarray(pressure_hpa, dtype=float)

    valid = np.isfinite(temperature) & np.isfinite(relative_humidity) & np.isfinite(pressure)
    if valid.sum() < 8:
        return np.nan

    temperature = temperature[valid]
    relative_humidity = relative_humidity[valid]
    pressure = pressure[valid]

    temperature_k = _to_kelvin(temperature)
    relative_humidity = np.clip(
        relative_humidity / 100.0 if np.nanmax(relative_humidity) > 1.5 else relative_humidity,
        0.001,
        1.0,
    )

    try:
        dewpoint = dewpoint_from_relative_humidity(
            temperature_k * units.kelvin,
            relative_humidity * units.dimensionless,
        )
        dewpoint_k = np.asarray(dewpoint.to("kelvin").magnitude, dtype=float)
    except Exception:
        return np.nan

    # Add the analyzed surface point when all three surface diagnostics are usable.
    if (
        np.isfinite(surface_pressure)
        and np.isfinite(surface_temperature)
        and np.isfinite(surface_dewpoint)
    ):
        psfc_hpa = _to_hpa(surface_pressure)
        if 800.0 <= psfc_hpa <= 1100.0:
            tsfc_k = float(_to_kelvin(np.asarray([surface_temperature]))[0])
            tdsfc_k = float(_to_kelvin(np.asarray([surface_dewpoint]))[0])
            tdsfc_k = min(tdsfc_k, tsfc_k)

            nearest = np.where(np.abs(pressure - psfc_hpa) <= 0.5)[0]
            if nearest.size:
                index = int(nearest[0])
                temperature_k[index] = tsfc_k
                dewpoint_k[index] = tdsfc_k
                pressure[index] = psfc_hpa
            else:
                pressure = np.append(pressure, psfc_hpa)
                temperature_k = np.append(temperature_k, tsfc_k)
                dewpoint_k = np.append(dewpoint_k, tdsfc_k)

    # Sounding calculations require pressure to decrease monotonically with height.
    order = np.argsort(pressure)[::-1]
    pressure = pressure[order]
    temperature_k = temperature_k[order]
    dewpoint_k = dewpoint_k[order]

    # Remove duplicate pressure levels while preserving the descending order.
    _, unique_indices = np.unique(pressure, return_index=True)
    unique_indices = np.sort(unique_indices)
    pressure = pressure[unique_indices]
    temperature_k = temperature_k[unique_indices]
    dewpoint_k = dewpoint_k[unique_indices]

    if pressure.size < 8 or pressure[0] - pressure[-1] < 100.0:
        return np.nan

    # Keep enough upper-air depth to avoid silently truncating deep CAPE profiles.
    if pressure[-1] > 300.0:
        return np.nan

    try:
        mlcape, _ = mixed_layer_cape_cin(
            pressure * units.hPa,
            temperature_k * units.kelvin,
            dewpoint_k * units.kelvin,
            depth=100 * units.hPa,
        )
        value = float(mlcape.to("joule / kilogram").magnitude)
        return max(value, 0.0) if np.isfinite(value) else np.nan
    except Exception:
        # Below-ground levels, incomplete profiles, or individual parcel failures
        # should leave only that gridpoint missing rather than aborting the domain.
        return np.nan


def _mlcape_from_pressure_profiles(
    ds: xr.Dataset,
    surface_pressure: xr.DataArray,
    surface_temperature: xr.DataArray,
    surface_dewpoint: xr.DataArray,
) -> xr.DataArray:
    """Calculate gridded 100-hPa MLCAPE directly from RAP thermodynamic profiles."""
    lev = _pressure_coord(ds)
    if "t" not in ds.data_vars or "r" not in ds.data_vars:
        raise ValueError("Temperature and relative humidity profiles are required for MLCAPE")

    levels = [float(p) for p in ds[lev].values if 100 <= float(p) <= 1000]
    if len(levels) < 8:
        raise ValueError("Insufficient RAP pressure levels between 1000 and 100 hPa for MLCAPE")

    temperature = ds["t"].sel({lev: levels})
    relative_humidity = ds["r"].sel({lev: levels})
    pressure_hpa = np.asarray(levels, dtype=float)

    mlcape = xr.apply_ufunc(
        _mlcape_profile_value,
        temperature,
        relative_humidity,
        surface_pressure,
        surface_temperature,
        surface_dewpoint,
        input_core_dims=[[lev], [lev], [], [], []],
        output_core_dims=[[]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float],
        kwargs={"pressure_hpa": pressure_hpa},
    ).rename("mlcape_jkg")

    mlcape.attrs.update(
        {
            "long_name": "100-hPa mixed-layer CAPE",
            "units": "J kg-1",
            "calculation_method": "MetPy mixed_layer_cape_cin",
            "mixed_layer_depth_hpa": 100,
            "native_rap_cape_used": False,
            "profile_source": "RAP pressure-level temperature and relative humidity",
            "surface_augmentation": (
                "RAP surface pressure plus 2-m temperature/dewpoint when available; "
                "otherwise lowest valid pressure level"
            ),
        }
    )
    return mlcape


def _dcape_profile_value(
    temperature_profile: np.ndarray,
    rh_profile: np.ndarray,
    pressure_hpa: np.ndarray,
) -> float:
    """Calculate DCAPE for one RAP gridpoint pressure profile."""
    temperature = np.asarray(temperature_profile, dtype=float)
    relative_humidity = np.asarray(rh_profile, dtype=float)
    pressure = np.asarray(pressure_hpa, dtype=float)

    valid = np.isfinite(temperature) & np.isfinite(relative_humidity) & np.isfinite(pressure)
    if valid.sum() < 5:
        return np.nan

    temperature = temperature[valid]
    relative_humidity = relative_humidity[valid]
    pressure = pressure[valid]

    # MetPy sounding calculations expect pressure ordered from high to low.
    order = np.argsort(pressure)[::-1]
    pressure = pressure[order]
    temperature = temperature[order]
    relative_humidity = relative_humidity[order]

    # DCAPE needs a low-level parcel path and the 700-500-hPa source layer.
    if pressure.max() < 850 or pressure.min() > 700:
        return np.nan

    if np.nanmax(temperature) < 150:
        temperature = temperature + 273.15

    if np.nanmax(relative_humidity) > 1.5:
        relative_humidity = relative_humidity / 100.0
    relative_humidity = np.clip(relative_humidity, 0.01, 1.0)

    try:
        temp_quantity = temperature * units.kelvin
        rh_quantity = relative_humidity * units.dimensionless
        dewpoint = dewpoint_from_relative_humidity(temp_quantity, rh_quantity)
        dcape, _, _ = downdraft_cape(pressure * units.hPa, temp_quantity, dewpoint)
        return float(dcape.to("joule / kilogram").magnitude)
    except Exception:
        # Some individual gridpoints can contain below-ground or otherwise
        # incomplete profiles. Keep those points missing without aborting the map.
        return np.nan


def _dcape_from_pressure_profiles(ds: xr.Dataset) -> xr.DataArray:
    """Calculate a gridded DCAPE field from RAP temperature/RH profiles."""
    lev = _pressure_coord(ds)
    if "t" not in ds.data_vars or "r" not in ds.data_vars:
        raise ValueError("Temperature and relative humidity profiles are required for DCAPE")

    levels = [float(p) for p in ds[lev].values if 500 <= float(p) <= 1000]
    if len(levels) < 5:
        raise ValueError("Insufficient RAP pressure levels between 1000 and 500 hPa for DCAPE")

    temperature = ds["t"].sel({lev: levels})
    relative_humidity = ds["r"].sel({lev: levels})
    pressure_hpa = np.asarray(levels, dtype=float)

    dcape = xr.apply_ufunc(
        _dcape_profile_value,
        temperature,
        relative_humidity,
        input_core_dims=[[lev], [lev]],
        output_core_dims=[[]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float],
        kwargs={"pressure_hpa": pressure_hpa},
    )
    return dcape.rename("dcape_jkg")


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

    Pressure-level thermodynamic terms and MLCAPE are calculated directly from RAP.
    Native diagnostic fields are still used where appropriate for other predictors;
    DCAPE is calculated from pressure profiles if a native decoded diagnostic is absent.
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

    # Surface fields make the 100-hPa mixed parcel start at the analyzed surface.
    # Exact short names are used to avoid accidentally grabbing another thermodynamic layer.
    surface_pressure = _diagnostic_or_nan(
        datasets,
        bbox,
        candidates=["sp", "pres"],
        template=template,
        name="surface_pressure",
    )
    surface_temperature = _diagnostic_or_nan(
        datasets,
        bbox,
        candidates=["2t", "t2m"],
        template=template,
        name="surface_temperature",
    )
    surface_dewpoint = _diagnostic_or_nan(
        datasets,
        bbox,
        candidates=["2d", "d2m"],
        template=template,
        name="surface_dewpoint",
    )

    fields["mlcape_jkg"] = _mlcape_from_pressure_profiles(
        iso,
        surface_pressure,
        surface_temperature,
        surface_dewpoint,
    )
    try:
        fields["mlcape_jkg"] = fields["mlcape_jkg"].interp_like(template)
    except Exception:
        pass

    fields["sbli_c"] = _diagnostic_or_nan(
        datasets,
        bbox,
        candidates=["lftx", "4lftx", "lifted index", "surface lifted index"],
        template=template,
        name="sbli_c",
    )

    native_dcape = _diagnostic_or_nan(
        datasets,
        bbox,
        candidates=["dcape", "downdraft cape"],
        template=template,
        name="dcape_jkg",
    )
    if bool(native_dcape.isnull().all()):
        native_dcape = _dcape_from_pressure_profiles(iso)
        try:
            native_dcape = native_dcape.interp_like(template)
        except Exception:
            pass
    fields["dcape_jkg"] = native_dcape.rename("dcape_jkg")

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

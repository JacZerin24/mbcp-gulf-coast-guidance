from __future__ import annotations

import numpy as np
import xarray as xr
from metpy.calc import (
    dewpoint_from_relative_humidity,
    downdraft_cape,
    equivalent_potential_temperature,
    lifted_index,
    mixed_layer_cape_cin,
    parcel_profile,
)
from metpy.units import units

from .rap import find_isobaric_dataset, standardize_lon, subset_domain


def _pressure_coord(ds: xr.Dataset) -> str:
    if "isobaricInhPa" not in ds.coords:
        raise ValueError("Dataset does not contain isobaricInhPa")
    return "isobaricInhPa"


def _pressure_levels(
    ds: xr.Dataset,
    minimum: float = 100.0,
    maximum: float = 1000.0,
) -> np.ndarray:
    lev = _pressure_coord(ds)
    values = np.asarray(ds[lev].values, dtype=float)
    values = values[
        np.isfinite(values) & (values >= minimum) & (values <= maximum)
    ]
    if values.size < 2:
        raise ValueError(
            f"Insufficient pressure levels between {minimum:g} and {maximum:g} hPa"
        )
    return values


def _at_pressure(da: xr.DataArray, level_hpa: float) -> xr.DataArray:
    """Return a field at an exact pressure, interpolating if the level is absent."""
    lev = "isobaricInhPa"
    levels = np.asarray(da[lev].values, dtype=float)
    exact = np.where(np.isclose(levels, level_hpa, atol=1e-6))[0]
    if exact.size:
        return da.isel({lev: int(exact[0])}).squeeze(drop=True)
    return da.interp({lev: float(level_hpa)}).squeeze(drop=True)


def _temperature_c(da: xr.DataArray) -> xr.DataArray:
    out = da.astype(float)
    finite = np.asarray(out.values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size and float(np.nanmedian(finite)) > 150.0:
        out = out - 273.15
    return out


def _height_field(ds: xr.Dataset) -> xr.DataArray:
    if "gh" in ds.data_vars:
        return ds["gh"].astype(float)
    if "z" in ds.data_vars:
        return (ds["z"].astype(float) / 9.80665).rename("gh")
    raise ValueError("RAP pressure-level geopotential height is required")


def _lapse_rate_pressure_layer(
    ds: xr.Dataset,
    bottom_hpa: float,
    top_hpa: float,
) -> xr.DataArray:
    temp = _temperature_c(ds["t"])
    height = _height_field(ds)
    tb = _at_pressure(temp, bottom_hpa)
    tt = _at_pressure(temp, top_hpa)
    zb = _at_pressure(height, bottom_hpa)
    zt = _at_pressure(height, top_hpa)
    dz_km = (zt - zb) / 1000.0
    return ((tb - tt) / dz_km).where(dz_km > 0)


def _all_names(variable_name: str, da: xr.DataArray) -> set[str]:
    attrs = da.attrs
    return {
        str(variable_name).lower(),
        str(attrs.get("GRIB_shortName", "")).lower(),
        str(attrs.get("GRIB_name", "")).lower(),
        str(attrs.get("long_name", "")).lower(),
        str(attrs.get("standard_name", "")).lower(),
    }


def _level_type(da: xr.DataArray) -> str:
    return str(da.attrs.get("GRIB_typeOfLevel", "")).lower()


def _level_value(da: xr.DataArray) -> float | None:
    for key in ("GRIB_level", "heightAboveGround"):
        value = da.attrs.get(key)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            pass
    if "heightAboveGround" in da.coords:
        try:
            value = np.asarray(da.coords["heightAboveGround"].values).squeeze()
            return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _find_grib_field(
    datasets: list[xr.Dataset],
    candidates: list[str],
    *,
    type_of_level: str | None = None,
    level: float | None = None,
) -> xr.DataArray | None:
    """Find a GRIB field with level constraints, preferring short-name matches."""
    candidate_names = {candidate.lower() for candidate in candidates}
    best: tuple[int, xr.DataArray] | None = None
    expected_type = type_of_level.lower() if type_of_level else None

    for ds in datasets:
        for variable_name, da in ds.data_vars.items():
            names = _all_names(variable_name, da)
            if not (names & candidate_names):
                continue

            actual_type = _level_type(da)
            if expected_type and actual_type and actual_type != expected_type:
                continue

            actual_level = _level_value(da)
            if (
                level is not None
                and actual_level is not None
                and not np.isclose(actual_level, level, atol=0.1)
            ):
                continue

            short_name = str(da.attrs.get("GRIB_shortName", "")).lower()
            score = 0
            if short_name in candidate_names:
                score += 5
            if str(variable_name).lower() in candidate_names:
                score += 4
            if expected_type and actual_type == expected_type:
                score += 4
            if (
                level is not None
                and actual_level is not None
                and np.isclose(actual_level, level, atol=0.1)
            ):
                score += 4

            candidate = da.squeeze(drop=True)
            if best is None or score > best[0]:
                best = (score, candidate)

    return None if best is None else best[1]


def _field_or_nan(
    datasets: list[xr.Dataset],
    bbox: dict,
    candidates: list[str],
    template: xr.DataArray,
    name: str,
    *,
    type_of_level: str | None = None,
    level: float | None = None,
) -> xr.DataArray:
    da = _find_grib_field(
        datasets,
        candidates,
        type_of_level=type_of_level,
        level=level,
    )
    if da is None:
        out = xr.full_like(template, np.nan, dtype=float)
        out.name = name
        return out
    da = subset_domain(da, bbox).astype(float).squeeze(drop=True)
    try:
        da = da.interp_like(template)
    except Exception:
        pass
    da.name = name
    return da


def _to_kelvin(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    finite = values[np.isfinite(values)]
    if finite.size and float(np.nanmedian(finite)) < 150.0:
        return values + 273.15
    return values


def _to_hpa(value: float) -> float:
    value = float(value)
    return value / 100.0 if value > 2000.0 else value


def _prepare_surface_anchored_profile(
    temperature_profile: np.ndarray,
    rh_profile: np.ndarray,
    pressure_hpa: np.ndarray,
    surface_pressure: float,
    surface_temperature: float,
    surface_dewpoint: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Build a monotonically decreasing p/T/Td profile beginning at the RAP surface."""
    temperature = np.asarray(temperature_profile, dtype=float)
    rh = np.asarray(rh_profile, dtype=float)
    pressure = np.asarray(pressure_hpa, dtype=float)

    valid = np.isfinite(temperature) & np.isfinite(rh) & np.isfinite(pressure)
    if valid.sum() < 8:
        return None

    pressure = pressure[valid]
    temperature_k = _to_kelvin(temperature[valid])
    rh = rh[valid]
    if np.nanmax(rh) > 1.5:
        rh = rh / 100.0
    rh = np.clip(rh, 0.001, 1.0)

    try:
        dewpoint = dewpoint_from_relative_humidity(
            temperature_k * units.kelvin,
            rh * units.dimensionless,
        )
        dewpoint_k = np.asarray(
            dewpoint.to("kelvin").magnitude,
            dtype=float,
        )
    except Exception:
        return None

    has_surface = (
        np.isfinite(surface_pressure)
        and np.isfinite(surface_temperature)
        and np.isfinite(surface_dewpoint)
    )
    if has_surface:
        psfc = _to_hpa(surface_pressure)
        if 800.0 <= psfc <= 1100.0:
            tsfc = float(_to_kelvin(np.asarray([surface_temperature]))[0])
            tdsfc = float(_to_kelvin(np.asarray([surface_dewpoint]))[0])
            tdsfc = min(tdsfc, tsfc)

            # Remove isobaric values below terrain before adding the surface point.
            keep = pressure <= psfc + 0.5
            pressure = pressure[keep]
            temperature_k = temperature_k[keep]
            dewpoint_k = dewpoint_k[keep]
            if pressure.size < 8:
                return None

            near = np.where(np.isclose(pressure, psfc, atol=0.5))[0]
            if near.size:
                i = int(near[0])
                pressure[i] = psfc
                temperature_k[i] = tsfc
                dewpoint_k[i] = tdsfc
            else:
                pressure = np.append(pressure, psfc)
                temperature_k = np.append(temperature_k, tsfc)
                dewpoint_k = np.append(dewpoint_k, tdsfc)

    order = np.argsort(pressure)[::-1]
    pressure = pressure[order]
    temperature_k = temperature_k[order]
    dewpoint_k = dewpoint_k[order]

    rounded_pressure = np.round(pressure, 6)
    _, first = np.unique(rounded_pressure, return_index=True)
    first = np.sort(first)
    pressure = pressure[first]
    temperature_k = temperature_k[first]
    dewpoint_k = dewpoint_k[first]

    valid = (
        np.isfinite(pressure)
        & np.isfinite(temperature_k)
        & np.isfinite(dewpoint_k)
    )
    pressure = pressure[valid]
    temperature_k = temperature_k[valid]
    dewpoint_k = dewpoint_k[valid]

    if pressure.size < 8 or pressure[0] - pressure[-1] < 100.0:
        return None
    if pressure[-1] > 300.0:
        return None

    return pressure, temperature_k, dewpoint_k


def _interp_pressure_scalar(
    pressure: np.ndarray,
    values: np.ndarray,
    target_hpa: float,
) -> float:
    order = np.argsort(pressure)
    p = np.asarray(pressure, dtype=float)[order]
    v = np.asarray(values, dtype=float)[order]
    if target_hpa < p[0] or target_hpa > p[-1]:
        return np.nan
    return float(np.interp(target_hpa, p, v))


def _profile_diagnostics_value(
    temperature_profile: np.ndarray,
    rh_profile: np.ndarray,
    surface_pressure: float,
    surface_temperature: float,
    surface_dewpoint: float,
    pressure_hpa: np.ndarray,
) -> tuple[float, float, float, float]:
    """Research-faithful MLCAPE, SBLI, DCAPE, and theta-e deficit for one grid point."""
    prepared = _prepare_surface_anchored_profile(
        temperature_profile,
        rh_profile,
        pressure_hpa,
        surface_pressure,
        surface_temperature,
        surface_dewpoint,
    )
    if prepared is None:
        return np.nan, np.nan, np.nan, np.nan

    pressure, temperature_k, dewpoint_k = prepared
    p = pressure * units.hPa
    t = temperature_k * units.kelvin
    td = dewpoint_k * units.kelvin

    mlcape_value = np.nan
    sbli_value = np.nan
    dcape_value = np.nan
    thetae_value = np.nan

    try:
        mlcape, _ = mixed_layer_cape_cin(
            p,
            t,
            td,
            depth=100 * units.hPa,
        )
        value = float(mlcape.to("joule / kilogram").magnitude)
        if np.isfinite(value):
            mlcape_value = max(value, 0.0)
    except Exception:
        pass

    try:
        profile = parcel_profile(p, t[0], td[0])
        li = lifted_index(p, t, profile)
        value = np.asarray(
            li.to("delta_degC").magnitude,
            dtype=float,
        ).reshape(-1)[0]
        if np.isfinite(value):
            sbli_value = float(value)
    except Exception:
        # Reproduce the research script's manual 500-hPa fallback.
        try:
            profile = parcel_profile(p, t[0], td[0])
            env500 = _interp_pressure_scalar(
                pressure,
                temperature_k - 273.15,
                500.0,
            )
            parcel500 = _interp_pressure_scalar(
                pressure,
                np.asarray(profile.to("degC").magnitude, dtype=float),
                500.0,
            )
            if np.isfinite(env500) and np.isfinite(parcel500):
                sbli_value = env500 - parcel500
        except Exception:
            pass

    try:
        result = downdraft_cape(p, t, td)
        dcape = result[0] if isinstance(result, tuple) else result
        value = float(dcape.to("joule / kilogram").magnitude)
        if np.isfinite(value):
            dcape_value = max(value, 0.0)
    except Exception:
        pass

    try:
        thetae = equivalent_potential_temperature(p, t, td).to("kelvin")
        thetae_k = np.asarray(thetae.magnitude, dtype=float)
        sfc_p = float(pressure[0])
        low = thetae_k[
            (pressure <= sfc_p + 1e-6)
            & (pressure >= sfc_p - 100.0 - 1e-6)
        ]
        mid = thetae_k[
            (pressure <= 700.0 + 1e-6)
            & (pressure >= 500.0 - 1e-6)
        ]
        if low.size and mid.size:
            value = float(np.nanmax(low) - np.nanmin(mid))
            if np.isfinite(value):
                thetae_value = value
    except Exception:
        pass

    return mlcape_value, sbli_value, dcape_value, thetae_value


def _profile_diagnostics_from_grid(
    ds: xr.Dataset,
    surface_pressure: xr.DataArray,
    surface_temperature: xr.DataArray,
    surface_dewpoint: xr.DataArray,
) -> dict[str, xr.DataArray]:
    lev = _pressure_coord(ds)
    if "t" not in ds.data_vars or "r" not in ds.data_vars:
        raise ValueError(
            "RAP temperature and relative-humidity profiles are required"
        )

    levels = _pressure_levels(ds, 100.0, 1000.0)
    temperature = ds["t"].sel({lev: levels})
    rh = ds["r"].sel({lev: levels})

    mlcape, sbli, dcape, thetae = xr.apply_ufunc(
        _profile_diagnostics_value,
        temperature,
        rh,
        surface_pressure,
        surface_temperature,
        surface_dewpoint,
        input_core_dims=[[lev], [lev], [], [], []],
        output_core_dims=[[], [], [], []],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float, float, float, float],
        kwargs={"pressure_hpa": np.asarray(levels, dtype=float)},
    )

    return {
        "mlcape_jkg": mlcape.rename("mlcape_jkg"),
        "sbli_c": sbli.rename("sbli_c"),
        "dcape_jkg": dcape.rename("dcape_jkg"),
        "thetae_deficit_k": thetae.rename("thetae_deficit_k"),
    }


def _surface_to_3km_value(
    temperature_profile: np.ndarray,
    height_profile: np.ndarray,
    surface_temperature: float,
    surface_height: float,
) -> float:
    """Research definition: (surface T - T at 3000 m AGL) / 3 km."""
    temperature = np.asarray(temperature_profile, dtype=float)
    height = np.asarray(height_profile, dtype=float)
    valid = np.isfinite(temperature) & np.isfinite(height)
    if (
        valid.sum() < 2
        or not np.isfinite(surface_temperature)
        or not np.isfinite(surface_height)
    ):
        return np.nan

    temperature_c = _to_kelvin(temperature[valid]) - 273.15
    height = height[valid]
    tsfc_c = float(
        _to_kelvin(np.asarray([surface_temperature]))[0] - 273.15
    )
    z_sfc = float(surface_height)
    target = z_sfc + 3000.0

    keep = height >= z_sfc - 10.0
    height = height[keep]
    temperature_c = temperature_c[keep]
    if height.size < 2:
        return np.nan

    order = np.argsort(height)
    height = height[order]
    temperature_c = temperature_c[order]
    unique_height, first = np.unique(
        np.round(height, 3),
        return_index=True,
    )
    temperature_c = temperature_c[first]
    height = unique_height
    if target < height[0] or target > height[-1]:
        return np.nan

    t3km = float(np.interp(target, height, temperature_c))
    return (tsfc_c - t3km) / 3.0


def _surface_to_3km_lapse_rate(
    ds: xr.Dataset,
    surface_temperature: xr.DataArray,
    surface_height: xr.DataArray,
) -> xr.DataArray:
    lev = _pressure_coord(ds)
    temperature = ds["t"]
    height = _height_field(ds)
    out = xr.apply_ufunc(
        _surface_to_3km_value,
        temperature,
        height,
        surface_temperature,
        surface_height,
        input_core_dims=[[lev], [lev], [], []],
        output_core_dims=[[]],
        vectorize=True,
        dask="parallelized",
        output_dtypes=[float],
    )
    return out.rename("sfc_3km_lapse_rate_c_km")


def calculate_environmental_fields(
    datasets: list[xr.Dataset],
    bbox: dict,
) -> dict[str, xr.DataArray]:
    """Calculate all seven predictors using the original project definitions.

    The definitions mirror ``pull_rap_bufkit_environment_for_report.py`` as
    closely as the gridded RAP pressure product allows. Native CAPE,
    lifted-index, and DCAPE diagnostics are not used as model inputs.
    """
    iso = subset_domain(find_isobaric_dataset(datasets), bbox)
    if "t" not in iso.data_vars or "r" not in iso.data_vars:
        raise ValueError(
            "RAP isobaric temperature and relative humidity are required"
        )

    temp_c = _temperature_c(iso["t"])
    template = _at_pressure(temp_c, 850.0)

    fields: dict[str, xr.DataArray] = {}
    fields["vertical_totals_850_500_c"] = (
        _at_pressure(temp_c, 850.0) - _at_pressure(temp_c, 500.0)
    ).rename("vertical_totals_850_500_c")
    fields["mid_level_lapse_rate_c_km"] = _lapse_rate_pressure_layer(
        iso,
        700.0,
        500.0,
    ).rename("mid_level_lapse_rate_c_km")

    surface_pressure = _field_or_nan(
        datasets,
        bbox,
        ["sp", "pres", "surface pressure"],
        template,
        "surface_pressure",
        type_of_level="surface",
    )
    surface_temperature = _field_or_nan(
        datasets,
        bbox,
        ["2t", "t2m", "2 metre temperature", "2 meter temperature"],
        template,
        "surface_temperature",
        type_of_level="heightAboveGround",
        level=2.0,
    )
    surface_dewpoint = _field_or_nan(
        datasets,
        bbox,
        [
            "2d",
            "d2m",
            "2 metre dewpoint temperature",
            "2 meter dewpoint temperature",
        ],
        template,
        "surface_dewpoint",
        type_of_level="heightAboveGround",
        level=2.0,
    )
    surface_height = _field_or_nan(
        datasets,
        bbox,
        [
            "orog",
            "gh",
            "hgt",
            "z",
            "geopotential height",
            "surface geopotential",
        ],
        template,
        "surface_height_m",
        type_of_level="surface",
    )

    surface_short_name = str(
        surface_height.attrs.get("GRIB_shortName", "")
    ).lower()
    surface_units = str(
        surface_height.attrs.get(
            "GRIB_units",
            surface_height.attrs.get("units", ""),
        )
    ).lower()
    if surface_short_name == "z" or (
        "m2" in surface_units and "s-2" in surface_units
    ):
        surface_height = surface_height / 9.80665

    missing_surface = {
        "surface pressure": surface_pressure,
        "2-m temperature": surface_temperature,
        "2-m dewpoint": surface_dewpoint,
        "surface/terrain height": surface_height,
    }
    missing_names = [
        name
        for name, da in missing_surface.items()
        if bool(da.isnull().all())
    ]
    if missing_names:
        raise ValueError(
            "Required RAP surface fields were not found: "
            + ", ".join(missing_names)
        )

    fields.update(
        _profile_diagnostics_from_grid(
            iso,
            surface_pressure,
            surface_temperature,
            surface_dewpoint,
        )
    )
    fields["sfc_3km_lapse_rate_c_km"] = _surface_to_3km_lapse_rate(
        iso,
        surface_temperature,
        surface_height,
    )

    lon_name = "longitude" if "longitude" in template.coords else "lon"
    for key, da in list(fields.items()):
        try:
            da = da.interp_like(template)
        except Exception:
            pass
        if lon_name in da.coords:
            da = da.assign_coords(
                {lon_name: standardize_lon(da[lon_name])}
            )
        fields[key] = da.astype(float).rename(key)

    missing = [
        key
        for key, da in fields.items()
        if bool(da.isnull().all())
    ]
    if missing:
        raise ValueError(
            "These required refined-model fields could not be calculated: "
            + ", ".join(missing)
        )

    return fields

from __future__ import annotations

import unittest

import numpy as np
import xarray as xr
from metpy.calc import relative_humidity_from_dewpoint
from metpy.units import units

from mbcp_guidance.fields import calculate_environmental_fields
from mbcp_guidance.rap import find_isobaric_dataset


class RapFieldIntegrationTests(unittest.TestCase):
    @staticmethod
    def _coords():
        latitude = xr.DataArray(
            np.asarray([[29.5, 29.5], [30.0, 30.0]]),
            dims=("y", "x"),
        )
        longitude = xr.DataArray(
            np.asarray([[-90.5, -90.0], [-90.5, -90.0]]),
            dims=("y", "x"),
        )
        return latitude, longitude

    def _isobaric_dataset(self):
        pressure = np.asarray(
            [
                1000, 975, 950, 925, 900, 875, 850, 825, 800, 775,
                750, 725, 700, 675, 650, 625, 600, 575, 550, 525,
                500, 475, 450, 425, 400, 375, 350, 325, 300, 275,
                250, 225, 200, 175, 150, 125, 100,
            ],
            dtype=float,
        )
        height = 44330.0 * (1.0 - (pressure / 1013.25) ** 0.1903)
        temperature_c = 30.0 - 6.4 * np.maximum(height - 100.0, 0.0) / 1000.0
        dewpoint_c = 23.0 - 1.8 * np.maximum(height - 100.0, 0.0) / 1000.0
        dewpoint_c = np.minimum(dewpoint_c, temperature_c - 0.2)

        temperature_k = temperature_c + 273.15
        dewpoint_k = dewpoint_c + 273.15
        rh = relative_humidity_from_dewpoint(
            temperature_k * units.kelvin,
            dewpoint_k * units.kelvin,
        ).to("percent").magnitude

        latitude, longitude = self._coords()
        shape = (pressure.size, 2, 2)
        t = np.broadcast_to(temperature_k[:, None, None], shape).copy()
        r = np.broadcast_to(rh[:, None, None], shape).copy()
        gh = np.broadcast_to(height[:, None, None], shape).copy()

        return xr.Dataset(
            {
                "t": (("isobaricInhPa", "y", "x"), t),
                "r": (("isobaricInhPa", "y", "x"), r),
                "gh": (("isobaricInhPa", "y", "x"), gh),
            },
            coords={
                "isobaricInhPa": pressure,
                "latitude": latitude,
                "longitude": longitude,
            },
        )

    def _surface_dataset(self, variable_name, values, short_name, level_type, level=None, units_name=None):
        latitude, longitude = self._coords()
        attrs = {
            "GRIB_shortName": short_name,
            "GRIB_typeOfLevel": level_type,
        }
        if level is not None:
            attrs["GRIB_level"] = float(level)
        if units_name:
            attrs["GRIB_units"] = units_name
        return xr.Dataset(
            {
                variable_name: xr.DataArray(
                    np.full((2, 2), float(values)),
                    dims=("y", "x"),
                    coords={"latitude": latitude, "longitude": longitude},
                    attrs=attrs,
                )
            }
        )

    def test_complete_isobaric_dataset_is_preferred(self):
        complete = self._isobaric_dataset()
        incomplete = complete[["t"]]
        selected = find_isobaric_dataset([incomplete, complete])
        self.assertIn("t", selected.data_vars)
        self.assertIn("r", selected.data_vars)
        self.assertTrue("gh" in selected.data_vars or "z" in selected.data_vars)

    def test_full_field_builder_accepts_cfgrib_style_rap_fields(self):
        iso = self._isobaric_dataset()

        # Deliberately place misleading 80-m PRES/TMP fields before the required
        # surface/2-m fields. Level constraints must prevent them from being used.
        pres80 = self._surface_dataset("pres80", 100000.0, "pres", "heightAboveGround", 80.0, "Pa")
        tmp80 = self._surface_dataset("t80", 302.0, "t", "heightAboveGround", 80.0, "K")
        surface_pressure = self._surface_dataset("sp", 100500.0, "sp", "surface", units_name="Pa")
        surface_height = self._surface_dataset("orog", 100.0, "orog", "surface", units_name="gpm")
        t2m = self._surface_dataset("t2m", 303.15, "t2m", "heightAboveGround", 2.0, "K")
        d2m = self._surface_dataset("d2m", 296.15, "d2m", "heightAboveGround", 2.0, "K")

        datasets = [pres80, tmp80, iso, surface_pressure, surface_height, t2m, d2m]
        fields = calculate_environmental_fields(
            datasets,
            {"south": 28.0, "north": 31.6, "west": -91.8, "east": -88.0},
        )

        expected = {
            "vertical_totals_850_500_c",
            "mlcape_jkg",
            "sbli_c",
            "mid_level_lapse_rate_c_km",
            "sfc_3km_lapse_rate_c_km",
            "dcape_jkg",
            "thetae_deficit_k",
        }
        self.assertEqual(set(fields), expected)

        for name, field in fields.items():
            values = np.asarray(field.values, dtype=float)
            self.assertEqual(values.shape, (2, 2), name)
            self.assertTrue(np.isfinite(values).all(), name)

        # Broad physical checks catch common unit/level-selection mistakes without
        # constraining legitimate meteorological variability.
        self.assertTrue((fields["mlcape_jkg"].values >= 0).all())
        self.assertTrue((fields["dcape_jkg"].values >= 0).all())
        self.assertTrue((fields["sfc_3km_lapse_rate_c_km"].values > 0).all())
        self.assertTrue((fields["vertical_totals_850_500_c"].values > 0).all())


if __name__ == "__main__":
    unittest.main()

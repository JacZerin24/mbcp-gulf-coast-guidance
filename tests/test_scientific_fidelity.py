from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import xarray as xr
from metpy.calc import (
    dewpoint_from_relative_humidity,
    downdraft_cape,
    equivalent_potential_temperature,
    lifted_index,
    mixed_layer_cape_cin,
    parcel_profile,
    relative_humidity_from_dewpoint,
)
from metpy.units import units

from mbcp_guidance.fields import (
    _at_pressure,
    _prepare_surface_anchored_profile,
    _profile_diagnostics_value,
    _surface_to_3km_value,
)
from mbcp_guidance.model import apply_refined_model


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "config" / "refined_gulf_coast_model.json"
FIXTURE_PATH = ROOT / "tests" / "data" / "refined_model_reference_cases.json"


class ModelRegressionTests(unittest.TestCase):
    def test_full_fit_probabilities_match_research_cases(self):
        model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cases = fixture["cases"]

        fields = {
            key: xr.DataArray(
                [case["predictors"][key] for case in cases],
                dims=("case",),
            )
            for key in model["variables"]
        }
        probability, index = apply_refined_model(fields, model)

        expected_probability = np.asarray(
            [case["expected_probability"] for case in cases],
            dtype=float,
        )
        expected_index = np.asarray(
            [case["expected_index"] for case in cases],
            dtype=int,
        )

        np.testing.assert_allclose(
            probability.values,
            expected_probability,
            rtol=0.0,
            atol=2e-12,
        )
        np.testing.assert_array_equal(
            index.values.astype(int),
            expected_index,
        )

    def test_probability_to_index_matches_project_numpy_rint(self):
        values = np.asarray([0.04, 0.05, 0.06, 0.14, 0.15, 0.16, 0.95, 1.0])
        expected = np.clip(np.rint(values * 10.0), 0, 10).astype(int)
        self.assertEqual(expected.tolist(), [0, 0, 1, 1, 2, 2, 10, 10])


class PredictorDefinitionTests(unittest.TestCase):
    def test_pressure_interpolation_is_exact_or_linear(self):
        da = xr.DataArray(
            np.asarray([10.0, 20.0, 30.0]),
            dims=("isobaricInhPa",),
            coords={"isobaricInhPa": [900.0, 800.0, 700.0]},
        )
        self.assertAlmostEqual(float(_at_pressure(da, 800.0)), 20.0, places=12)
        self.assertAlmostEqual(float(_at_pressure(da, 850.0)), 15.0, places=12)

    def test_surface_to_3km_lapse_rate_uses_true_agl_target(self):
        height = np.asarray([100.0, 1000.0, 2000.0, 3100.0, 4000.0])
        temp_c = 30.0 - 7.0 * (height - 100.0) / 1000.0
        value = _surface_to_3km_value(
            temp_c + 273.15,
            height,
            30.0 + 273.15,
            100.0,
        )
        self.assertAlmostEqual(value, 7.0, places=12)

    def test_profile_diagnostics_match_original_metpy_definitions(self):
        pressure = np.asarray(
            [
                1000, 975, 950, 925, 900, 850, 800, 750, 700,
                650, 600, 550, 500, 450, 400, 350, 300, 250, 200,
            ],
            dtype=float,
        )
        temperature_c = np.asarray(
            [
                30.0, 28.5, 27.0, 25.5, 24.0, 20.5, 17.0, 13.5, 10.0,
                6.5, 3.0, -0.5, -4.0, -8.0, -12.0, -17.0, -22.0, -28.0, -35.0,
            ]
        )
        dewpoint_c = np.asarray(
            [
                23.0, 22.0, 21.0, 19.5, 18.0, 14.0, 10.0, 6.0, 2.0,
                -3.0, -8.0, -13.0, -18.0, -24.0, -30.0, -36.0, -42.0, -49.0, -56.0,
            ]
        )
        temperature_k = temperature_c + 273.15
        dewpoint_k = dewpoint_c + 273.15
        rh = relative_humidity_from_dewpoint(
            temperature_k * units.kelvin,
            dewpoint_k * units.kelvin,
        ).to("dimensionless").magnitude

        actual = _profile_diagnostics_value(
            temperature_k,
            rh,
            100000.0,
            temperature_k[0],
            dewpoint_k[0],
            pressure,
        )

        prepared = _prepare_surface_anchored_profile(
            temperature_k,
            rh,
            pressure,
            100000.0,
            temperature_k[0],
            dewpoint_k[0],
        )
        self.assertIsNotNone(prepared)
        p_hpa, t_k, td_k = prepared
        p = p_hpa * units.hPa
        t = t_k * units.kelvin
        td = td_k * units.kelvin

        mlcape, _ = mixed_layer_cape_cin(p, t, td, depth=100 * units.hPa)
        parcel = parcel_profile(p, t[0], td[0])
        sbli = lifted_index(p, t, parcel)
        dcape = downdraft_cape(p, t, td)[0]
        thetae = equivalent_potential_temperature(p, t, td).to("kelvin").magnitude
        low = thetae[(p_hpa <= p_hpa[0]) & (p_hpa >= p_hpa[0] - 100.0)]
        mid = thetae[(p_hpa <= 700.0) & (p_hpa >= 500.0)]

        expected = (
            float(mlcape.to("joule / kilogram").magnitude),
            float(np.asarray(sbli.to("delta_degC").magnitude).reshape(-1)[0]),
            float(dcape.to("joule / kilogram").magnitude),
            float(np.nanmax(low) - np.nanmin(mid)),
        )

        np.testing.assert_allclose(
            np.asarray(actual, dtype=float),
            np.asarray(expected, dtype=float),
            rtol=0.0,
            atol=1e-9,
        )


if __name__ == "__main__":
    unittest.main()

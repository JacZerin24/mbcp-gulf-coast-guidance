# Model notes

## Product meaning

This repository implements the seven-predictor Gulf Coast damaging-wind probability framework developed from the warm-season wind/null research dataset. The output is **conditional environmental favorability for damaging convective wind if thunderstorms develop or are ongoing**. It is not a convective-initiation forecast and it is not official NWS operational guidance.

## Research source of truth

The scientific-fidelity implementation is based on the final project artifacts:

- `MBCP_Probabilistic_Dataset.xlsm`
- `pull_rap_bufkit_environment_for_report.py`
- `mbcp_shared.py`
- `03_logistic_models_v2_roc_labels.py`
- the final `mbcp_results` model/verification outputs

The research dataset contains 287 cases: 179 damaging-wind cases and 108 convective-null cases. All seven selected refined-model predictors are populated for all 287 cases.

## Exact predictor definitions

| Predictor | Units | Research definition used by the live gridded implementation |
|---|---:|---|
| `vertical_totals_850_500_c` | °C | Temperature interpolated to 850 hPa minus temperature interpolated to 500 hPa. |
| `mlcape_jkg` | J/kg | `metpy.calc.mixed_layer_cape_cin` using the lowest 100 hPa of the surface-anchored pressure/temperature/dewpoint profile. |
| `sbli_c` | °C | Surface parcel from `metpy.calc.parcel_profile`, then `metpy.calc.lifted_index`; equivalent fallback is environmental T500 minus surface-parcel T500. |
| `mid_level_lapse_rate_c_km` | °C/km | `(T700 - T500) / ((Z500 - Z700) / 1000)`, with T and Z evaluated at exactly/interpolated 700 and 500 hPa. |
| `sfc_3km_lapse_rate_c_km` | °C/km | Surface temperature minus temperature interpolated to 3000 m AGL, divided by 3 km. |
| `dcape_jkg` | J/kg | `metpy.calc.downdraft_cape` from the surface-anchored pressure/temperature/dewpoint profile. Native RAP DCAPE is not used as the model predictor. |
| `thetae_deficit_k` | K | Maximum theta-e in the lowest 100 hPa above actual surface pressure minus minimum theta-e from 700-500 hPa. |

The gridded implementation uses RAP pressure-level temperature/RH plus surface pressure, 2-m temperature/dewpoint, and terrain height to reproduce these definitions as closely as the gridded pressure product permits. Pressure levels below terrain are removed before parcel calculations.

## Exact deployed logistic model

The original analysis pipeline was:

1. `SimpleImputer(strategy="median")`
2. `StandardScaler()`
3. `LogisticRegression(penalty="l2", C=1.0, solver="lbfgs", max_iter=5000)`

Because the seven selected predictors have no missing values in the final 287-case dataset, the median imputer did not change the fitted values for this model.

For each predictor:

```text
z_i = (x_i - training_mean_i) / training_std_i
```

Then:

```text
logit = intercept + sum(coefficient_i * z_i)
probability = 1 / (1 + exp(-logit))
index = clip(rint(probability * 10), 0, 10)
```

The live configuration stores the full-precision training means, StandardScaler scales, coefficients, and intercept reproduced from the final workbook. Presentation-rounded coefficients are not used in the calculation.

## Research validation versus deployment fit

The research skill estimates were produced with 5-fold stratified cross-validation (`shuffle=True`, `random_state=42`):

| Metric | Refined seven-predictor model |
|---|---:|
| AUC | 0.892148 |
| Brier score | 0.119041 |
| Log loss | 0.392075 |
| Average precision | 0.911703 |
| Mean predicted probability | 0.622503 |
| Observed wind frequency | 0.623693 |

These are **cross-validated research performance metrics**, not independent external or real-time verification. New RAP guidance is correctly generated with the final model fitted on all 287 research cases.

## Automated fidelity checks

`tests/test_scientific_fidelity.py` protects the scientific implementation by checking:

- the exact full-fit model against representative original-workbook cases spanning roughly 2% to 98% probability;
- the 0-10 `numpy.rint(probability * 10)` transform;
- exact/interpolated pressure-level sampling;
- the true 3000-m-AGL lapse-rate calculation;
- MLCAPE, SBLI, DCAPE, and theta-e deficit against the same MetPy definitions used by the research extraction.

The Pages deployment workflow runs these tests before it can regenerate guidance. A separate pull-request workflow also runs them for scientific/model changes.

## Remaining validation step

The **definitions and statistical model are now designed to reproduce the project**. The remaining scientific validation is a data-representation comparison:

> At historical wind/null cases, how closely do the gridded RAP calculations reproduce the original point-based RAP BUFKIT values stored in the research workbook?

That comparison should be performed predictor by predictor before the product is described as independently validated operational guidance. Differences can arise because the original research sampled nearby BUFKIT sounding sites, while the live product calculates fields on the RAP grid.

## Suggested presentation wording

> The real-time prototype now uses the same predictor definitions and exact full-data logistic fit as the final Gulf Coast research analysis. Remaining validation focuses on how closely gridded RAP predictor calculations reproduce the original point-based RAP BUFKIT case values.

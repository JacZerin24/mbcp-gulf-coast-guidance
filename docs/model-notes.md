# Model notes

## What this project currently represents

This repository is an experimental implementation of the Gulf Coast microburst/damaging-wind probability framework developed from the warm-season wind/null dataset.

The model output should be treated as **conditional environmental favorability** for damaging convective wind if thunderstorms develop or are ongoing.

## Prototype model

The first model included in this repository is the refined Gulf Coast greedy logistic model.

Required variables:

| Variable | Units | Notes |
|---|---:|---|
| `vertical_totals_850_500_c` | deg C | 850-500 mb temperature difference |
| `mlcape_jkg` | J/kg | Mixed-layer CAPE |
| `sbli_c` | deg C | Surface-based lifted index |
| `mid_level_lapse_rate_c_km` | deg C/km | Prototype pressure-level lapse rate |
| `sfc_3km_lapse_rate_c_km` | deg C/km | Prototype near-surface to ~3 km lapse rate |
| `dcape_jkg` | J/kg | Downdraft CAPE |
| `thetae_deficit_k` | K | Prototype low-level to mid-level theta-e deficit |

The model uses standardized values:

```text
z = (value - training_mean) / training_standard_deviation
```

Then applies:

```text
p = 1 / (1 + exp(-logit))
```

The 0-10 index is currently:

```text
index = round(probability * 10)
```

## Important validation issue

The original research dataset used point-based RAP sounding extraction. This repository applies the model to gridded RAP data. Before treating maps as meaningful, compare gridded variables against the original spreadsheet values at known wind/null case points.

Highest-priority checks:

1. Vertical totals from gridded RAP versus sounding-extracted values.
2. MLCAPE from gridded RAP diagnostics versus sounding-extracted MLCAPE.
3. DCAPE from RAP diagnostics versus sounding-extracted DCAPE.
4. Theta-e deficit calculation definition.
5. Surface-to-3 km lapse rate approximation over the low terrain near LIX.

## Suggested presentation wording

> These results support the feasibility of a future real-time RAP-based probabilistic microburst guidance tool. The current repository is an experimental prototype for translating the research model into hourly gridded 0-10 conditional damaging-wind potential.

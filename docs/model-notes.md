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
| `mlcape_jkg` | J/kg | 100-hPa mixed-layer CAPE calculated from RAP thermodynamic profiles |
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

## Real-time MLCAPE calculation

The web guidance no longer selects a generic/native RAP CAPE diagnostic for the `mlcape_jkg` predictor.

For every RAP grid point, the implementation now:

1. Uses the RAP pressure-level temperature and relative-humidity profile.
2. Converts RH to dewpoint with MetPy.
3. Adds RAP surface pressure plus 2-m temperature/dewpoint when those decoded fields are available, so the parcel layer begins at the analyzed surface.
4. Otherwise begins at the lowest valid RAP pressure level.
5. Calculates mixed-layer CAPE with `metpy.calc.mixed_layer_cape_cin` using an explicit 100-hPa mixed-layer depth.
6. Uses that unsmoothed calculated MLCAPE directly in the standardized logistic model.

Each successful deployment records the MLCAPE method and domain min/median/max in `web/data/latest.json`. The native RAP CAPE diagnostic is explicitly marked as unused for this predictor.

This is a substantially more controlled implementation than the earlier generic `cape` field lookup, but it still needs case-by-case comparison with the original point-based research extraction before the real-time index should be considered fully validated.

## Important validation issue

The original research dataset used point-based RAP sounding extraction. This repository applies the model to gridded RAP data. Before treating maps as fully validated, compare gridded variables against the original spreadsheet values at known wind/null case points.

Highest-priority checks:

1. Vertical totals from gridded RAP versus sounding-extracted values.
2. Calculated 100-hPa MLCAPE versus the original sounding-extracted MLCAPE.
3. Surface-based lifted index definition and RAP field selection.
4. DCAPE from RAP diagnostics versus sounding-extracted DCAPE.
5. Theta-e deficit calculation definition.
6. Surface-to-3 km lapse rate approximation over the low terrain near LIX.

## Suggested presentation wording

> These results support the feasibility of a future real-time RAP-based probabilistic microburst guidance tool. The current repository is an experimental prototype for translating the research model into hourly gridded 0-10 conditional damaging-wind potential.

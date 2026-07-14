# mbcp-gulf-coast-guidance

Experimental Gulf Coast microburst / damaging-wind guidance for the NWS New Orleans/Baton Rouge (LIX) area.

This repository is a prototype for turning the MBCP Gulf Coast research dataset into an hourly RAP-based web display. The goal is to produce a **conditional 0–10 damaging wind / pulse microburst favorability index** over the LIX CWA using the 0-hour RAP analysis.

> **Status:** research prototype. This is not official NWS operational guidance.

## Concept

The current research workflow trained an initial probabilistic framework using warm-season damaging-wind reports and null convection cases. The prototype applies that framework to gridded RAP analysis data:

```text
0-hr RAP analysis
  -> gridded thermodynamic fields
  -> refined Gulf Coast logistic model
  -> damaging-wind probability
  -> experimental 0-10 index
  -> GeoJSON contours + GitHub Pages web map
```

The output should be interpreted as:

> **Conditional damaging-wind potential if thunderstorms develop or are ongoing.**

It does not forecast convective initiation by itself.

## Refined model variables

The first version of the refined Gulf Coast model uses:

- vertical totals, 850-500 mb temperature difference
- MLCAPE
- surface-based lifted index
- mid-level lapse rate
- surface-to-3 km lapse rate
- DCAPE
- theta-e deficit

Model coefficients, training means, and training standard deviations are stored in:

```text
config/refined_gulf_coast_model.json
```

## Repository layout

```text
config/                  Domain and model configuration
mbcp_guidance/            Python package for RAP processing and index generation
scripts/                  Command-line entry points
web/                      Static GitHub Pages app
docs/                     Notes, caveats, and roadmap
.github/workflows/        Hourly/manual GitHub Actions workflow
```

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run once locally

Using the latest RAP f00 analysis that Herbie can find:

```bash
python scripts/run_guidance.py --output-dir web/data --asset-dir web/assets
```

Using a local GRIB2 file that you already downloaded:

```bash
python scripts/run_guidance.py --grib path/to/rap.tHHz.awp130pgrbf00.grib2 --output-dir web/data --asset-dir web/assets
```

Expected outputs:

```text
web/data/latest.json
web/data/index_contours.geojson
web/data/probability_contours.geojson
web/assets/latest_index.png
```

## GitHub Pages

After merging the scaffold, enable GitHub Pages from the repository settings:

1. Go to **Settings -> Pages**.
2. Select **Deploy from a branch**.
3. Use branch `main` and folder `/web`, or configure a Pages workflow later.

## Automation

The included workflow is located at:

```text
.github/workflows/update_guidance.yml
```

It can be run manually from the GitHub Actions tab and is also scheduled hourly. The first few runs should be treated as debugging runs because RAP GRIB decoding and derived-field naming can vary by environment.

## Scientific caveats

- The model was trained on point cases, not full spatial grids.
- Wind cases are damaging-wind reports, not all confirmed microbursts.
- The output is conditional environmental favorability, not storm occurrence.
- The 0-10 index is experimental and should be validated on independent events before operational use.
- RAP-derived fields may not exactly match the original RAP sounding extraction workflow used in the research dataset.

## Recommended next steps

1. Validate a few known wind/null days with archived RAP files.
2. Confirm the RAP-derived grid variables match the spreadsheet/sounding-extraction values at nearby points.
3. Add CWA/parish outlines to the web map.
4. Add an archive of past cycles.
5. Compare hourly output with radar/LSR cases during future warm-season events.

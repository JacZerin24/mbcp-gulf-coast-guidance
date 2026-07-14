# Local test checklist

Use this before relying on the hourly workflow.

## 1. Create environment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 2. Run with automatic latest RAP download

```bash
python scripts/run_guidance.py --output-dir web/data --asset-dir web/assets
```

## 3. Run with a local RAP file

```bash
python scripts/run_guidance.py --grib path/to/rap.tHHz.awp130pgrbf00.grib2 --output-dir web/data --asset-dir web/assets
```

## 4. Expected files

```text
web/data/latest.json
web/data/index_contours.geojson
web/data/probability_contours.geojson
web/assets/latest_index.png
```

## 5. Most likely first errors

- `Could not find an isobaric pressure-level RAP dataset`  
  The RAP product or GRIB group did not decode the expected pressure-level fields.

- `These required fields were not available/calculable...`  
  One or more diagnostic fields such as MLCAPE, LI, or DCAPE need a different RAP GRIB short-name search or a custom calculation.

- `eccodes` / `cfgrib` import errors  
  Install ECMWF ecCodes system libraries or use a conda environment.

## 6. Scientific sanity checks

- Does vertical totals generally increase in hotter/deeper lapse-rate environments?
- Are index values highest where instability/lapse-rate fields are strongest?
- Do results line up with known high-end wind days from the research dataset?
- Are large areas of missing data present? If so, inspect GRIB variable naming.

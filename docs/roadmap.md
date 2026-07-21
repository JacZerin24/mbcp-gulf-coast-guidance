# Roadmap

## Phase 1: Repository scaffold

- [x] Store refined model coefficients and training standardization values.
- [x] Add RAP GRIB opening/downloading helper code.
- [x] Add gridded environmental field calculation scaffold.
- [x] Add model-to-probability/index conversion.
- [x] Add static Leaflet web map.
- [x] Add GitHub Actions workflow for hourly/manual runs.

## Phase 2: Local validation

- [ ] Run `scripts/run_guidance.py` on one archived RAP file.
- [ ] Confirm all required fields are found in the RAP GRIB inventory.
- [ ] Compare gridded fields against the completed research spreadsheet at known case locations.
- [ ] Adjust field definitions/search terms where RAP diagnostic naming differs.
- [ ] Confirm output contours render correctly on the local web page.

## Phase 3: Real-time debugging

- [ ] Run the GitHub Action manually.
- [ ] Fix any dependency issues from `cfgrib`, `eccodes`, `cartopy`, or `Herbie`.
- [ ] Confirm `web/data/latest.json` updates with the latest RAP cycle.
- [ ] Confirm GitHub Pages displays the latest contours.

## Phase 4: Science improvements

- [ ] Add independent-year validation.
- [ ] Add storm-mode/radar context.
- [ ] Test whether the index should be conditional on reflectivity or lightning.
- [ ] Compare with observed LSRs after future warm-season events.
- [ ] Explore separate pulse-only filtering based on 0-6 km shear.

## Phase 5: Operational prototype improvements

- [ ] Add CWA boundary, parish/county outlines, and major highways.
- [ ] Add cycle archive and trend display.
- [ ] Add probability layer legend and index legend.
- [ ] Add a timestamp/status banner when RAP data are stale.
- [ ] Export GeoJSON so the output can be consumed by other local tools.

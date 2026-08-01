# Project intent: ESA BIOMASS Gamma0 MGRS DPS

Build a MAAP DPS/OGC Application Package that transforms ESA BIOMASS L1B Beta0
amplitudes into fixed-grid products on standard 100 km MGRS tiles. Each
accepted `(source granule, MGRS tile)` pair writes four Beta0 COGs, four linear
Gamma0 COGs, one resampled GammaNought COG, one display-only RGB thumbnail, and
one STAC Item.

`dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md` defines the product contract.
`dev-docs/plans/2026-07-31-001-feat-source-package-dps-plan.md` defines the
package implementation plan. Update both deliberately when changing a contract
or architectural decision.

## Current state and migration boundary

- `main.py` remains the native-radar-grid diagnostic reference. It can
  authenticate and cache source assets for local testing, validates physical LUT
  alignment, and writes GCP-referenced diagnostic COGs.
- Production code receives staged local paths for a source STAC Item JSON,
  Beta0 TIFF, radiometry LUT NetCDF, annotation XML, and study-tiles vector. It
  must not authenticate, search STAC, download assets, or make HTTP requests.
- Native-grid diagnostics are not production assets. Do not advertise them as a
  tile collection, stack them with fixed-grid products, or use them in temporal
  composites.
- Extract shared calibration behavior without regressing `main.py`'s
  radar-space LUT-alignment checks.

## Required processing workflow

1. Validate the staged source Item and five local inputs. Require the Item's
   `enclosure_tiff`, `enclosure_nc`, and `enclosure_annot_xml` assets; retain
   source ID, collection, time, bbox, self link, and asset URLs after stripping
   user info, query parameters, and fragments.
2. Use the source WGS84 bbox to enumerate candidate standard 100 km MGRS tiles.
   Use the staged study geometry only to filter candidates. `mgrs` remains the
   source for tile IDs, UTM CRS, hemisphere, and exact 100,000 m bounds. Reject
   antimeridian and polar bboxes in this UTM-only release.
3. Create each exact north-up `4000 × 4000` target grid at 25 m. Densify the
   tile perimeter, transform it to the Beta0 GCP CRS, and back-project it with a
   GDAL GCP transformer. Pad and clip the radar window; skip it when no valid
   overlap exists.
4. Read only the four-band Beta0 window. Derive its azimuth and slant-range
   coordinates from the annotation's azimuth interval, range-pixel spacing, and
   ground-to-slant polynomial. Read a bracketed `radiometry/gammaNought` LUT
   slice and bilinearly sample it onto the window.
5. Convert Beta0 nodata to `NaN` and calculate linear Gamma0:
   `Beta0.astype("float32") ** 2 * gammaNought`.
6. Shift full-image GCP pixel coordinates into the local-window frame. Warp the
   four Beta0 polarizations, four Gamma0 polarizations, and resampled GammaNought
   directly to the fixed tile UTM grid. Each scientific output gets one bilinear
   radar-window-to-tile-grid interpolation. Skip a tile with no valid scientific
   output.
7. Validate and write nine tiled, DEFLATE-compressed, single-band `float32`
   COGs and one RGB thumbnail in a sibling temporary directory. Write
   `item.json` after validating every asset, atomically promote the leaf
   directory, then rebuild the local STAC Catalog and Collection from valid
   products.

STAC search, authentication, and asset download belong upstream. For local
validation, `main.py` may materialize the source Item and three granule assets;
the caller stages the study vector before invoking the production path.

## Output contract

```text
<output-root>/<mgrs-tile>/<acquisition-date>/<source-item-id>/
  beta0_hh.tif
  beta0_hv.tif
  beta0_vh.tif
  beta0_vv.tif
  gamma0_hh.tif
  gamma0_hv.tif
  gamma0_vh.tif
  gamma0_vv.tif
  gamma_nought.tif
  thumbnail.png
  item.json
<output-root>/catalog.json
<output-root>/collection.json
```

- Scientific COGs use `NaN` during processing and `-9999.0` (`float32`) nodata
  when written. Gamma0 is linear intensity, never dB.
- Each COG has one band, 512-pixel blocks, DEFLATE compression, an affine
  north-up UTM transform, and the MGRS-derived EPSG code. All nine share grid
  metadata and baseline valid-footprint geometry where source values are valid.
- `thumbnail.png` is a display-only RGB image. It does not replace a scientific
  asset or claim COG, Raster, or SAR properties.
- Name Items `gamma0-<source-item-id>-<mgrs-tile-id>` in the
  `biomass-gamma0-mgrs-25m` Collection. Include Projection, Raster, and SAR
  metadata where applicable, source and derived-from links, source time, MGRS
  tile, processing provenance, and sanitized source URLs.
- Validate Items, Catalog, and Collection with PySTAC. A valid Item with its
  nine COGs and thumbnail is complete unless `--overwrite` is set. Rebuild root
  STAC files from valid nested Items after every run; write a valid empty Catalog
  and Collection when a source produces no accepted tile.

## OGC Application Package structure

Follow the wrapper shape of sibling `../esa-biomass-dps`; use this package's
staged-input and uv runtime contract:

```text
algorithm.yml                    # MAAP metadata, input schema, resources, commands
esa-biomass-gamma0.cwl           # CWL Workflow + CommandLineTool, returning ./output
build.sh                          # install frozen uv production environment
run.sh                            # create ./output; forward staged paths to run.py
run.py                            # thin CLI adapter; no processing or downloads
src/esa_biomass_gamma0/          # reusable production workflow package
  calibration.py
  cli.py
  grids.py
  raster.py
  source.py
  stac.py
  workflow.py
tests/                            # deterministic synthetic tests
main.py                           # retained diagnostic reference
```

Do not duplicate processing logic across `run.sh`, `run.py`, CWL, notebooks, or
`main.py`. Shell and CWL files adapt runtime inputs only; package Python owns
the workflow. `algorithm.yml`, CWL, shell wrappers, and the CLI use
`source_item`, `beta0_tiff`, `radiometry_lut`, `annotation_xml`, `study_tiles`,
`resolution` (fixed to 25 m), and `overwrite` with matching defaults. The five
path inputs stage as OGC `File` values. `run.py` receives local paths. CWL
returns `./output` as a `Directory` and disables network access.

`pyproject.toml` and `uv.lock` are the sole production dependency definition and
lock. `build.sh` uses `uv sync --frozen --no-dev`; `run.sh` uses
`uv run --frozen --no-dev`. Do not add conda manifests, a second package manager,
or a bespoke execution framework.

## Guardrails

- The source bbox and study geometry filter candidates only. Exact GCP
  back-projection determines coverage.
- Never derive MGRS geometry from parsed IDs, hard-coded extents, or an
  approximate fishnet. Never use a study-tile index as authoritative MGRS
  geometry.
- LUT dimensions remain `(azimuth, range)`. Never transpose, flip, or scale its
  full extent to Beta0 pixels. Do not densify the complete LUT for one tile.
- Gamma0 calibration belongs in radar geometry. A later GCP warp cannot repair
  incorrect LUT sampling.
- Never write an intermediate GCP COG and warp it again. Each scientific output
  receives one direct bilinear warp from the local radar window to its fixed UTM
  grid.
- Do not combine tiles in different UTM CRSs into a common stack in this POC.
- Never hard-code or commit secrets. The production package requires no MAAP
  credentials. A local staging adapter must not place credentials or signed URLs
  in logs, config, COG tags, STAC JSON, or provenance.
- Use temporary leaf directories and atomic promotion. Keep incomplete outputs
  unregistered and identifiable for cleanup.
- Retain runnable shape, nodata, target-grid, and LUT-alignment assertions.
  Validate against MAAP-backed data before promoting a diagnostic helper.

## Minimum validation

Keep deterministic tests for:

- staged-source validation, including source identity, time, bbox, required
  asset entries, five readable files, URL sanitization, and no-network behavior;
- MGRS target-grid CRS, bounds, transform, and `4000 × 4000` shape in both
  hemispheres, including candidate and study-geometry filtering;
- GCP boundary back-projection, padding, clipping, rejection, and local-GCP
  shifting with synthetic GCPs;
- physical-coordinate LUT interpolation and `NaN`-preserving Gamma0 math;
- direct warp and validation of nine COGs, including grid metadata, compression,
  nodata, quantity, and polarization tags, plus RGB thumbnail validation;
- STAC assets, source links, time, projection/raster/SAR metadata, empty
  results, idempotency, overwrite safety, and Catalog rebuild recovery; and
- DPS input/default parity, staged `File` semantics, output `Directory`, and
  disabled network access.

Before release promotion, compare a windowed-LUT result with the full-frame
diagnostic reference. Valid-pixel Gamma0 differences must remain below `1e-3`
after matching calculation and resampling conventions. Test a swath-edge and a
swath-interior tile against map features and record positional residuals.

## Documentation responsibilities

Update `README.md` when the package layout, runtime interface, product contract,
or implementation status changes. Keep the workflow specification as the source
for detailed design decisions, validation gates, and open questions.

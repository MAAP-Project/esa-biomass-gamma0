# Project intent: ESA BIOMASS Gamma0 MGRS DPS

Build a MAAP DPS/OGC Application Package that transforms ESA BIOMASS L1B Beta0
amplitudes into analysis-ready, linear-Gamma0 COGs on standard 100 km MGRS
tiles. The package writes four single-band outputs (HH, HV, VH, VV) and one
STAC Item for every accepted `(source granule, MGRS tile)` pair.

`dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md` is the detailed product
contract. This file records implementation guardrails. When they disagree, the
specification wins until this file is updated deliberately.

## Current state and migration boundary

- `main.py` is the native-radar-grid diagnostic reference. It can authenticate
  and cache source assets for local testing, validates physically aligned LUT
  sampling, then writes GCP-referenced diagnostic COGs.
- The production runner receives a staged source STAC Item JSON, Beta0 TIFF,
  radiometry LUT NetCDF, and annotation XML as local paths. It must not
  authenticate, search STAC, or download granule assets.
- Diagnostic COGs are not production assets. Do not advertise them as a tile
  collection, stack them with fixed-grid outputs, or use them for analysis-ready
  temporal composites.
- Preserve this diagnostic path while extracting production helpers. Production
  code must not regress its radar-space LUT alignment checks.

## Required processing workflow

1. Receive a staged source STAC Item JSON and the three staged local asset
   files: `enclosure_tiff` (Beta0), `enclosure_nc` (radiometry LUT), and
   `enclosure_annot_xml` (annotation). Validate the Item's required asset
   entries and that every staged file is readable; retain source Item JSON/self
   link, ID, collection, time, and asset URLs as provenance.
2. Use the Item WGS84 bbox only to enumerate candidate standard 100 km MGRS
   tiles. Use `mgrs` to derive tile IDs, UTM CRS, hemisphere, and exact
   100,000 m bounds. Reject antimeridian and polar bboxes in this UTM-only POC.
3. For each candidate, create its exact north-up 1,000 × 1,000 target grid at
   100 m. Densify the tile perimeter, transform it to the Beta0 GCP CRS, and
   back-project it with a GDAL GCP transformer. Pad and clip the resulting
   radar window; skip it when no valid GCP overlap exists.
4. Read only that four-band Beta0 window. Derive window azimuth time and
   slant-range time from the annotation's azimuth interval, range-pixel
   spacing, and ground-to-slant polynomial. Read the bracketed
   `radiometry/gammaNought` slice and bilinearly sample it onto the window.
5. Convert Beta0 nodata to `NaN` and calculate linear Gamma0:
   `Beta0.astype("float32") ** 2 * gammaNought`.
6. Shift full-image GCP pixel coordinates into the local-window frame. Warp
   every polarization directly from those shifted GCPs to the fixed tile UTM
   grid with one bilinear interpolation. Skip a tile when all outputs are
   nodata.
7. Validate and write one tiled, DEFLATE-compressed, single-band `float32` COG
   per polarization. Write `item.json` only after all four assets validate,
   then update the local STAC Catalog and Collection.

STAC search, authentication, and asset download are upstream staging concerns.
For local tests, reuse `main.py`'s cache/download helpers to create the same
four inputs, then invoke the production path with their local paths.

## Output contract

```text
<output-root>/<mgrs-tile>/<acquisition-date>/<source-item-id>/
  gamma0_hh.tif
  gamma0_hv.tif
  gamma0_vh.tif
  gamma0_vv.tif
  item.json
<output-root>/catalog.json
<output-root>/collection.json
```

- COGs are linear Gamma0 intensity, not dB; use `NaN` during calculation and
  `-9999.0` (`float32`) as final nodata.
- Every COG has one band, 512-pixel blocks, DEFLATE compression, an affine
  north-up UTM transform, and its MGRS-derived EPSG code. All four assets share
  identical grid metadata and valid-data-mask geometry except for legitimate
  polarization-specific invalid values.
- Create one STAC Item named `gamma0-<source-item-id>-<mgrs-tile-id>`, in the
  `biomass-gamma0-mgrs-100m` Collection. Populate Projection, Raster, and SAR
  extension fields where available, source/derived-from links, source time,
  MGRS tile, and processing provenance.
- Validate Catalog, Collection, and Items with PySTAC. An existing valid Item
  and its four valid assets is complete unless `--overwrite` is requested.

## OGC Application Package structure

Follow the package shape of the sibling `../esa-biomass-dps` project. The
production layout is intentionally small:

```text
algorithm.yml       # MAAP metadata, input schema, resources, commands
<package>.cwl       # CWL Workflow + CommandLineTool, returning ./output
environment.yml     # DPS conda runtime dependencies
build.sh            # build/update the named conda environment
run.sh              # create ./output; pass staged paths to run.py
run.py              # parse and validate CLI inputs; call production code only
gamma0_mgrs/        # reusable production processing helpers
tests/              # deterministic synthetic tests
main.py             # retained diagnostic reference
```

Do not duplicate processing logic across `run.sh`, `run.py`, CWL, notebooks,
or `main.py`. Shell and CWL files adapt the runtime only; Python owns the
workflow. `algorithm.yml` and CWL must expose the same inputs and defaults:
`source_item`, `beta0_tiff`, `radiometry_lut`, `annotation_xml`, `study_tiles`,
`resolution` (fixed to 100 m for v1), and `overwrite`. The first four are OGC
`File` inputs staged into the job; `run.py` receives their local paths and makes
no HTTP requests. The runtime writes to `./output` and CWL exposes that
directory.

Keep `environment.yml` (DPS runtime) and `pyproject.toml` (local `uv`
development) aligned when production dependencies change. Do not add a second
package manager or a bespoke execution framework.

## Guardrails

- The source bbox filters candidates only. Exact GCP back-projection determines
  coverage.
- Never derive MGRS geometry from a parsed ID, a hard-coded extent, or an
  approximate fishnet. Do not use a study-tile index as the authoritative MGRS
  extent.
- LUT dimensions remain `(azimuth, range)`. Never transpose, flip, or scale its
  full extent to Beta0 pixels. Do not densify the complete LUT to process one
  tile.
- Gamma0 calibration belongs in radar geometry. A later GCP warp cannot repair
  incorrect LUT sampling.
- Never first write a GCP COG and then warp it. Each production polarization
  gets exactly one bilinear warp from calibrated local radar pixels to the
  fixed UTM grid.
- Do not combine tiles in different UTM CRSs into a common stack in this POC.
- Never hard-code or commit secrets. The production package requires no MAAP
  credentials because its granule inputs are staged. Credentials used by a local
  staging adapter may not enter logs, config, COG tags, STAC JSON, or provenance
  URLs.
- Use temporary paths and atomic promotion for COGs; leave incomplete outputs
  unregistered and identifiable for cleanup.
- Retain runnable shape, nodata, target-grid, and LUT-alignment assertions.
  Validate against MAAP-backed data before promoting a diagnostic helper.

## Minimum validation

Keep deterministic tests for:

- staged-input validation, including source Item identity/time/bbox and the
  required three local granule files;
- MGRS target-grid CRS, bounds, transform, and `1000 × 1000` shape in both
  hemispheres;
- candidate selection, GCP boundary back-projection, padding, clipping, and
  local-GCP shifting using synthetic GCPs;
- physical-coordinate LUT interpolation and `NaN`-preserving Gamma0 math;
- COG band count, CRS, transform, compression, nodata, and polarization tags;
- STAC assets, source links, time, and projection/raster/SAR metadata.

Before scaling, compare a windowed-LUT result with the full-frame reference:
valid-pixel Gamma0 differences must stay below `1e-3` after matching
calculation and resampling conventions. Test a swath-edge and a swath-interior
tile against map features and record positional residuals.

## Documentation responsibilities

Update `README.md` when the package layout, runtime interface, product contract,
or implementation status changes. Keep the workflow specification as the
source for detailed design decisions, validation gates, and open questions.

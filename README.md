# ESA BIOMASS Gamma0 MGRS DPS

A MAAP DPS/OGC Application Package in development for converting staged ESA
BIOMASS Level-1B Beta0 amplitudes to linear Gamma0 on fixed, native-UTM 100 km
MGRS tiles. It emits four single-band Cloud Optimized GeoTIFFs (HH, HV, VH,
and VV) and one STAC Item for each source-granule × MGRS-tile pair.

The product design is defined in
[`dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md`](dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md).
That document is the detailed implementation contract; this README is the
project map.

## Status

This repository is in the migration from a calibration diagnostic to the
production tile product:

- `main.py` is the current diagnostic reference. It can download one L1B item
  for local testing, aligns the radiometry LUT in radar geometry, calculates
  Gamma0, and writes native-grid, GCP-referenced diagnostic COGs.
- The production runner will receive a source STAC Item JSON, Beta0 TIFF,
  radiometry LUT NetCDF, and annotation XML as staged local inputs. It must not
  download granule assets itself.
- The MGRS-windowing, fixed-UTM warp, single-band production COG, STAC, and DPS
  package entry points are planned work. Native-GCP diagnostic COGs are not
  analysis-ready tile products and must not be mixed with the future collection.

## Product contract

For every accepted source item and intersecting standard 100 km MGRS tile, the
production package will:

1. Receive one staged source STAC Item JSON, Beta0 TIFF, radiometry NetCDF LUT,
   and annotation XML. The Item supplies source identity, acquisition time,
   bbox, and provenance; the three assets are already local files.
2. Use the source bbox only to find candidate MGRS tiles. Back-projecting a
   densified tile boundary through the Beta0 GCP transformer decides actual
   coverage.
3. Read a padded local Beta0 window, sample only its required LUT region in
   radar coordinates, and calculate `Gamma0 = Beta0_amplitude² × gammaNought`.
4. Warp each polarization once, with bilinear resampling, directly to that
   tile's exact 1,000 × 1,000, north-up 100 m UTM grid.
5. Write four validated single-band `float32` COGs and an output STAC Item.

Source discovery, authentication, and remote asset retrieval happen upstream of
the DPS invocation. An orchestrator can search MAAP STAC and submit one staged
input set per selected L1B Item.

The output layout is:

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

COGs use linear Gamma0 intensity, `-9999.0` nodata, DEFLATE compression,
512-pixel tiles, and the MGRS-derived UTM CRS and transform. Calculations use
`NaN`. Each STAC Item has the four Gamma0 assets and preserves source,
projection, raster, SAR, and processing provenance.

## DPS / OGC Application Package layout

The production interface will follow the MAAP package shape used by the sibling
[`esa-biomass-dps`](../esa-biomass-dps) project, while retaining this
product's fixed-MGRS contract:

```text
algorithm.yml             # MAAP algorithm metadata, resources, and inputs
<package>.cwl             # OGC Application Package / CWL wrapper
environment.yml           # Conda environment installed in the DPS runtime
build.sh                  # creates or updates that environment
run.sh                    # creates ./output and passes staged paths to run.py
run.py                    # thin CLI adapter; no processing logic or downloads
gamma0_mgrs/              # production discovery, calibration, warp, COG, STAC code
tests/                    # synthetic unit tests and small integration coverage
main.py                   # retained native-grid diagnostic reference
```

Only `main.py`, `pyproject.toml`, and the specification currently exist from
that target layout. Add the package files together when the production path is
implemented; do not make `run.py` a second implementation of the processing
workflow.

The intended package inputs are:

| Input | Type | Meaning |
| --- | --- | --- |
| `source_item` | File | Staged source STAC Item JSON. |
| `beta0_tiff` | File | Staged `enclosure_tiff` asset. |
| `radiometry_lut` | File | Staged `enclosure_nc` asset. |
| `annotation_xml` | File | Staged `enclosure_annot_xml` asset. |
| `study_tiles` | File | Boreal study-area/tile-index file used to constrain processing. |
| `resolution` | number | Product resolution in metres. Version 1 accepts only `100`. |
| `overwrite` | boolean | Reprocess a valid existing source-item × tile output. |

`algorithm.yml` and the CWL tool must expose the same input names, `File`
staging semantics, and defaults. `run.py` receives their local paths and never
performs HTTP requests or token exchange. The package writes only to `./output`;
`run.sh` passes this location to the runner, and CWL returns it as a `Directory`.
Resource limits and the base container belong in `algorithm.yml` and CWL, not in
Python.

## Development

Local development currently uses the `uv` project environment. `main.py` may
materialize local test inputs with its `fetch_assets`, `cache_paths`, and
`write_cached_asset` helpers; this is a local-test adapter, not production-runner
behavior. It needs MAAP credentials supplied outside version control:

```bash
export ESA_MAAP_CLIENT_SECRET=...
export ESA_OFFLINE_TOKEN=...
uv run python main.py '<stac-item-id>' --out-dir diagnostics
```

Source assets are cached in `/tmp` by default; use `--cache-dir` to choose a
persistent cache. The diagnostic writes `beta0.tif`, `lut_native.tif`,
`lut_resampled.tif`, and `gamma0.tif` on their native GCP-referenced radar
grids.

When the DPS package is added, keep `environment.yml` as the package-runtime
manifest and keep it aligned with the local dependencies in `pyproject.toml`.
The production runner receives staged files and needs no MAAP credentials.
Credentials used by a local staging adapter must never appear in package
metadata, logs, STAC JSON, or committed configuration.

## Non-negotiable processing rules

- Derive MGRS IDs, UTM zones, and exact tile extents with `mgrs`; do not parse
  IDs, use a hard-coded fishnet, or rely on a tile-index geometry for extents.
- Calibrate in radar geometry before geocoding. A later GCP warp cannot repair
  a LUT aligned to the wrong radar coordinates.
- Preserve LUT axis order `(azimuth, range)`; do not transpose, flip, or stretch
  the complete LUT across Beta0 pixels.
- Do not combine products from different tile UTM CRSs before they have reached
  their own fixed target grids.
- Write and validate all four COGs before registering `item.json`. A valid item
  plus its four valid assets is idempotently complete unless `--overwrite` is
  set.

## Next implementation milestones

1. Extract and test windowed LUT sampling and synthetic GCP-window helpers from
   the diagnostic path.
2. Build one staged-source-item × one-MGRS-tile path that writes four fixed-grid
   COGs.
3. Add candidate tile discovery and local STAC Catalog/Collection serialization.
4. Add the DPS package files, including four staged granule inputs plus the STAC
   Item input, and validate the CWL/runtime contract on MAAP.

See [`AGENTS.md`](AGENTS.md) for contributor guardrails and the full workflow
specification for validation gates and unresolved metadata decisions.

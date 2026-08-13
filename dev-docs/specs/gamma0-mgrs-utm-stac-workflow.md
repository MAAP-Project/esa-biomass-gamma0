# Spec: BIOMASS Gamma0 MGRS UTM/STAC Workflow

## Context

The current prototype calculates Gamma0 in the BIOMASS L1B radar grid and writes GCP-referenced diagnostic COGs. Those outputs preserve source pixels, but acquisitions do not share a map grid. Consumers must GCP-warp them before temporal compositing or fusion with spectral data.

This workflow produces fixed-grid, 25 m MGRS tile products from one staged source granule. Each accepted `(source granule, MGRS tile)` pair produces four Beta0 COGs, four linear-Gamma0 COGs, one resampled GammaNought COG, and one display-only RGB thumbnail. A STAC Item describes the product. The native radar-grid Gamma0 remains a diagnostic, never a production asset.

## Goals

- Convert HH, HV, VH, and VV Beta0 amplitude data to linear Gamma0 with the paired radiometry LUT.
- Produce nine single-band scientific COGs on the exact 100 km MGRS tile grid for each accepted tile.
- Use the tile's UTM CRS and a deterministic `4000 × 4000` grid at 25 m.
- Create one STAC Item per `(source granule, MGRS tile)` pair and a local Catalog with direct Item links.
- Retain acquisition time, projection details, nodata semantics, processing metadata, and sanitized source provenance.
- Process a source STAC Item JSON, Beta0 TIFF, radiometry NetCDF, and annotation XML as staged local inputs. The staged workflow makes no network requests or credential lookups; its CWL permits network access only for MAAP `File` staging. A separate authenticated fetch algorithm materializes those same local inputs from a source Item ID.

## Non-goals

- Temporal composites, biomass-model features, or mosaics of overlapping granules.
- A global equal-area output grid, remote STAC publication, or publication credentials.
- Production use of native-grid diagnostic COGs.
- Statistics assets, a parallel tile executor, plugin system, configuration framework, or custom retry layer.
- A conda runtime manifest or a second dependency lock. `pyproject.toml` and `uv.lock` define the runtime.

## Constraints and Assumptions

- Output resolution is exactly 25 m, close to the source Beta0 25 m ground-range and 22.36 m azimuth sampling.
- `mgrs` derives each standard 100 km tile's ID, UTM CRS, hemisphere, and exact 100,000 m bounds. Code must not parse tile IDs or use an approximate fishnet as authoritative geometry.
- The output grid has shape `[4000, 4000]`.
- The radiometry NetCDF provides `geometry/longitude` and `geometry/latitude` LUTs aligned to the validated `(azimuth, range)` axes. They map Beta0 source pixels to WGS84 position after physical-coordinate interpolation.
- The source STAC bbox filters candidates only. Geometry-LUT overlap determines whether a tile has coverage.
- A granule may span adjacent UTM zones, but boundary selection must retain no more than one edge MGRS tile column per zone. Do not create extra neighboring-zone columns solely because a full UTM tile grid overhangs that zone boundary. This matches the HLS-observed boundary-tile policy and limits redundant products while retaining boundary coverage.
- Gamma0 is linear intensity: `Beta0_amplitude² × gammaNought`. It is not dB.
- Processing arrays use `NaN` for missing values. Written COGs use `-9999.0` as `float32` nodata.
- A source granule may yield no accepted tiles. That run still writes a valid empty Catalog.

## Architecture Overview

```text
staged algorithm: source Item JSON + Beta0 TIFF + LUT NetCDF + annotation XML
fetch algorithm: source Item ID -> authenticate + search + download to job-local storage
  -> DPS staged-source workflow (local paths only)
       -> validate source metadata and staged files
       -> read and validate radiometry and geometry LUT coordinates
       -> find source-bbox candidates
       -> for each source-item × MGRS-tile pair
            -> select a padded Beta0 pixel window from geometry-LUT coverage
            -> range-read the four-band Beta0 window
            -> sample GammaNought and longitude/latitude onto that window in physical coordinates
            -> calculate windowed Gamma0
            -> directly warp Beta0, Gamma0, and GammaNought from longitude/latitude to the tile grid
            -> validate nine single-band COGs and create an RGB thumbnail
            -> write and validate the STAC Item, then atomically promote it
       -> rebuild local STAC Catalog with direct Item links from valid Item directories
```

The package must not build a virtual stack from radar-geometry data. Consumers may stack only assets already warped to an identical tile grid.

## Processing Design

### 1. Materialize one source granule

1. `esa_biomass_gamma0_staged` accepts the source Item JSON, `enclosure_tiff` (Beta0), `enclosure_nc` (radiometry LUT), and `enclosure_annot_xml` (annotation) as OGC `File` inputs. Its CWL permits network access for MAAP `File` staging, while the staged workflow itself retrieves no credentials and makes no network requests.
2. `esa_biomass_gamma0_fetch` accepts one `BiomassLevel1b` Item ID. It retrieves `ESA_MAAP_CLIENT_SECRET` and `ESA_OFFLINE_TOKEN` through `MAAP().secrets.get_secret`, exchanges them for an access token, finds the Item, and downloads those four local inputs into job-local temporary storage. It must never log, serialize, tag, or persist secrets, tokens, or signed URLs.
3. Both forms pass the same four local paths to the staged-source workflow. That workflow validates the source ID, acquisition datetime, horizontal bbox, required Item asset entries, and readable regular staged files. It rejects antimeridian and polar bboxes for this UTM-only release.
4. The workflow retains source ID, collection, time, Item self link, and required asset URLs as provenance after removing URL user info, query parameters, and fragments.

For local development, `process-gamma0 local <item-id>` reuses the authenticated materialization adapter and its persistent cache before calling the staged workflow adapter; `main.py` reuses the same cache helper for native-grid diagnostics. No form accepts an AOI or study-vector input, and no form accepts a mixture of an Item ID and partial staged source files.

### 2. Select MGRS output tiles

1. Use `mgrs` to enumerate standard 100 km MGRS tiles only in UTM zones intersecting the source bbox. At a UTM boundary, retain at most the one edge tile column from each intersecting zone; reject additional neighboring-zone columns that arise only from full-grid overhang. Filter the retained tiles' WGS84 envelopes against the source bbox; do not probe neighboring zones.
2. Derive each retained tile's UTM zone, hemisphere, EPSG code, and exact 100 km bounds through `MGRSToUTM`.
3. Read the geometry LUT's WGS84 `geometry/longitude` and `geometry/latitude` arrays and verify that both use the same `(azimuth, range)` dimensions as `gammaNought`.
4. For each candidate, transform geometry-LUT nodes to the tile UTM CRS, select nodes inside the exact tile bounds, then map their physical LUT coordinates back to source Beta0 pixels.
5. Pad the resulting radar window, clip it to the source raster, and skip the tile when no valid geometry-LUT node overlaps.

The source bbox reduces work. The geometry-LUT overlap check remains authoritative.

### 3. Read and calibrate a source window

1. Read the accepted four-polarization Beta0 window from the staged TIFF.
2. Parse the staged annotation XML for the azimuth interval, range-pixel spacing, and ground-to-slant polynomial.
3. Open the staged NetCDF LUT, validate its `(azimuth, range)` coordinate axes, then convert full-source line and sample coordinates for the padded window to physical azimuth and slant-range time.
4. Read a one-cell-bracketed `radiometry/gammaNought` slice and bilinearly sample it onto the Beta0 window. Do not transpose, flip, scale full LUT extents, or interpolate the full LUT for a tile window.
5. Bilinearly sample `geometry/longitude` and `geometry/latitude` onto the same Beta0 window. These geometry arrays, never `gammaNought`, define georeferencing.
6. Convert Beta0 nodata to `NaN` and calculate Gamma0 for each polarization.

```python
window_gamma0 = window_beta0.astype("float32") ** 2 * window_gamma_nought
```

### 4. Warp to the fixed MGRS grid

Warp each of the four local Beta0 polarizations, four calibrated Gamma0 polarizations, and the resampled GammaNought window directly from the local longitude/latitude geometry arrays to the fixed MGRS grid:

- destination CRS: the tile UTM EPSG code;
- destination bounds: exact `mgrs`-derived tile bounds;
- destination transform: north-up, 25 m pixels aligned to the tile bounds;
- destination shape: `4000 × 4000`;
- resampling: bilinear;
- source nodata: `NaN`;
- destination nodata: `NaN` during computation and `-9999.0` when written.

Each scientific output has one radar-window-to-tile-grid interpolation. The workflow must not write an intermediate georeferenced COG and warp it later. It must not use cubic or average resampling unless a later scientific validation decision changes this specification.

A partial source footprint leaves nodata in uncovered portions of the complete fixed tile extent.

### 5. Write and validate tile assets

For every accepted pair, stage this product directory beside its final destination:

```text
<output-root>/<mgrs-tile>/<acquisition-date>/<source-item-id>/
  <source-item-id>-<mgrs-tile>-beta0_hh.tif
  <source-item-id>-<mgrs-tile>-beta0_hv.tif
  <source-item-id>-<mgrs-tile>-beta0_vh.tif
  <source-item-id>-<mgrs-tile>-beta0_vv.tif
  <source-item-id>-<mgrs-tile>-gamma0_hh.tif
  <source-item-id>-<mgrs-tile>-gamma0_hv.tif
  <source-item-id>-<mgrs-tile>-gamma0_vh.tif
  <source-item-id>-<mgrs-tile>-gamma0_vv.tif
  <source-item-id>-<mgrs-tile>-gamma0_lut.tif
  <source-item-id>-<mgrs-tile>-thumbnail.png
  item.json
```

The nine GeoTIFFs must be:

- single-band `float32` scientific rasters;
- tiled with 512-pixel blocks and DEFLATE compression;
- valid Cloud Optimized GeoTIFFs;
- north-up with an affine UTM transform, not GCP-only georeferencing;
- assigned the tile EPSG code and `-9999.0` nodata;
- tagged with quantity, units, source Item ID, and processing version; and
- tagged with polarization for Beta0 and Gamma0 assets.

Asset filenames include the source Item ID and MGRS tile so they are unique within a job output. All nine COGs share CRS, bounds, transform, width, height, and nodata. Their COG tags use `QUANTITY=beta0_amplitude`, `QUANTITY=gamma0_linear_intensity`, or `QUANTITY=gamma_nought_calibration_factor`, plus `UNITS=1`, source Item ID, processing version, and polarization where applicable. GammaNought is a single-band calibration-factor asset. The PNG thumbnail is display-only `uint8` RGB: Gamma0 HH, HV, and VV form red, green, and blue channels respectively, each stretched independently from its finite 2nd to 98th percentile. It does not replace a scientific asset or claim COG properties.

Validate all COGs and the thumbnail before writing `item.json` and atomically promoting the staged directory.

## STAC Design

### Catalog

Create a self-contained local Catalog rooted at `<output-root>/catalog.json`.
After every run, scan nested output directories for valid Items with their
required nine COGs and thumbnail, then atomically replace the Catalog with
relative `item` links to those valid leaf products. This recovers a complete
product after an interrupted Catalog update.

When no Item exists, write a valid empty Catalog. Product Items are standalone:
they have no `collection` field or Collection link. Rebuilding removes a stale
root `collection.json`. Validate the Catalog and Items with PySTAC before
reporting success. Use the Projection, Raster, SAR, and Processing extensions
where fields are populated. The temporary Collection used by the PgSTAC loader
declares MAAP Project as its processor, records the package under
`processing:software`, and links its source repository with the
`processing-software` relation.

### Item identity and geometry

Create one Item per source granule and MGRS tile:

```text
id: gamma0-<source-item-id>-<mgrs-tile-id>
datetime: source acquisition datetime
geometry: intersection of source coverage and tile footprint
bbox: bbox of geometry
```

When a reliable source-coverage polygon cannot be built from the sampled geometry-LUT window boundary, use full-tile geometry and set `maap:partial_coverage=true`. This fallback affects Item geometry only; the raster grid remains the full tile.

Required Item properties include:

```json
{
  "mgrs:utm_zone": 32,
  "mgrs:latitude_band": "T",
  "mgrs:grid_square": "PR",
  "datetime": "<source acquisition datetime>",
  "platform": "BIOMASS",
  "instruments": ["P-SAR"],
  "sar:instrument_mode": "P-SAR",
  "sar:frequency_band": "P",
  "sar:polarizations": ["HH", "HV", "VH", "VV"],
  "sar:product_type": "Gamma0",
  "processing:software": {
    "esa-biomass-gamma0": "<package version>"
  },
  "proj:epsg": "<tile UTM EPSG>",
  "proj:shape": [4000, 4000],
  "proj:transform": [25.0, 0.0, "<xmin>", 0.0, -25.0, "<ymax>"]
}
```

Use PySTAC's MGRS and SAR extensions for their standard fields and the
Processing extension for software provenance. The full tile ID is the zero-padded
`mgrs:utm_zone` followed by `mgrs:latitude_band` and `mgrs:grid_square`; it is
not stored as a project-specific field. The current workflow does not copy
source orbit, pass direction, or processing-baseline properties.

Add a `derived_from` link to the sanitized input Item self HREF when available,
sanitized `via` links to the Beta0 TIFF and radiometry NetCDF source URLs, and a
`processing-software` link to the package repository.

### Assets

Each Item has nine scientific COG data assets and one display-only thumbnail:

| Asset key | Filename | Quantity | Polarization | Roles |
|---|---|---|---|---|
| `beta0_hh` | `<source-item-id>-<mgrs-tile>-beta0_hh.tif` | Beta0 amplitude | HH | `data` |
| `beta0_hv` | `<source-item-id>-<mgrs-tile>-beta0_hv.tif` | Beta0 amplitude | HV | `data` |
| `beta0_vh` | `<source-item-id>-<mgrs-tile>-beta0_vh.tif` | Beta0 amplitude | VH | `data` |
| `beta0_vv` | `<source-item-id>-<mgrs-tile>-beta0_vv.tif` | Beta0 amplitude | VV | `data` |
| `gamma0_hh` | `<source-item-id>-<mgrs-tile>-gamma0_hh.tif` | linear Gamma0 intensity | HH | `data` |
| `gamma0_hv` | `<source-item-id>-<mgrs-tile>-gamma0_hv.tif` | linear Gamma0 intensity | HV | `data` |
| `gamma0_vh` | `<source-item-id>-<mgrs-tile>-gamma0_vh.tif` | linear Gamma0 intensity | VH | `data` |
| `gamma0_vv` | `<source-item-id>-<mgrs-tile>-gamma0_vv.tif` | linear Gamma0 intensity | VV | `data` |
| `gamma0_lut` | `<source-item-id>-<mgrs-tile>-gamma0_lut.tif` | bilinearly resampled Gamma0 multiplicative factor | n/a | `data` |
| `thumbnail` | `<source-item-id>-<mgrs-tile>-thumbnail.png` | display-only RGB composite | n/a | `thumbnail`, `overview` |

Each scientific asset includes its media type, Projection extension fields, a one-band Raster extension entry, and SAR polarization metadata where applicable. Raster-band metadata uses `float32`, `-9999.0` nodata, and unit `1`; the Gamma0 asset titles and COG quantity tags identify linear intensity. The thumbnail has image media type and thumbnail/overview roles; its PNG pixels carry RGB color interpretation but the STAC asset has no scientific Raster or SAR claims.

## Package and Runtime Interface

The installable package lives under `src/esa_biomass_gamma0/`. It owns one staged-source workflow API and a Typer CLI with `staged`, `fetch`, and development-only `local` commands. Runtime wrappers and diagnostic adapters call package code instead of duplicating processing logic.

The staged command handles one staged source granule:

```text
process-gamma0 staged \
  --source-item path/to/source-item.json \
  --beta0-tiff path/to/enclosure.tif \
  --radiometry-lut path/to/enclosure.nc \
  --annotation-xml path/to/annotation.xml \
  --output-root output/
```

| Setting | Meaning |
|---|---|
| `source_item` | Staged source STAC Item JSON with identity, time, bbox, and provenance |
| `beta0_tiff` | Staged `enclosure_tiff` file |
| `radiometry_lut` | Staged `enclosure_nc` file |
| `annotation_xml` | Staged `enclosure_annot_xml` file |
| `output_root` | Output directory, fixed to `./output` by the DPS wrapper |
| `window_padding_pixels` | Staged-CLI radar-window safety margin for scientific tuning; DPS wrappers use the default of 64 pixels |
| `processing_version` | Internal `process_source` parameter, defaulting to the installed package version stored in COG and STAC metadata |

Two direct OGC Application Packages expose the modes:
`dps/staged/esa-biomass-gamma0-staged.cwl` registers
`esa_biomass_gamma0_staged` for the four staged `File` inputs, and
`dps/fetch/esa-biomass-gamma0-fetch.cwl` registers
`esa_biomass_gamma0_fetch` for its `item_id` input. Both always produce 25 m
products, expose no resolution or overwrite control, return `./output` as a
`Directory`, and call the same local-file workflow. Both CWLs permit network
access: staged for MAAP `File` staging and fetch for authenticated source
materialization. The staged workflow itself remains credential-free and local-path-only.

CWL is the sole deployment contract. Release Please turns conventional commits
on `main` into a reviewed release PR, updating the package version and annotated
release fields in the repository-owned CWLs. Its GitHub Release validates those
CWLs, builds separate immutable staged and fetch GHCR images, then registers or
updates both release-commit-pinned raw CWL URLs at the MAAP production OGC
Processes endpoint. `latest` images from `main` are development-only and never
appear in a CWL. There is no legacy MAAP descriptor or package-build path.

Each CWL calls a thin `run.sh` and `run.py` adapter. The image build installs
its frozen production environment from `pyproject.toml` and `uv.lock`; fetch
selects the locked `fetch` extra. Each runtime wrapper selects the appropriate
command from the same package Typer app, creates `./output`, and contains no
scientific processing logic. The fetch command materializes temporary files and
calls the staged workflow adapter directly, rather than reconstructing staged
CLI arguments. The fetch runtime includes only the authentication,
STAC-discovery, download, and MAAP-secret dependencies required to materialize
the staged files; the staged runtime remains credential-free. The `local`
command reuses the common materialization adapter and persistent cache for local
Item-ID runs. `pyproject.toml` exposes the one `process-gamma0` entrypoint, and
it and `uv.lock` remain the only dependency definition and lock.

### Local eoAPI integration

The repository's development-only `docker-compose.yml` runs PgSTAC, STAC API,
TiTiler, and STAC Browser on the standard eoAPI ports. It mounts the local
output root read-only into TiTiler at `/data/gamma0`; `GAMMA0_OUTPUT_ROOT`
overrides the default `./output` host path.

`scripts/load_pgstac.py` reads the root Catalog's direct Item links. At load
time only, it creates the Collection PgSTAC requires, maps each relative local
asset HREF to a `file:///data/gamma0/...` URI, and assigns that Collection only
to the database copy. Generated Item and Catalog files retain relative asset
HREFs and remain portable outside Docker. Use the `pypgstac[psycopg]==0.9.11`
extra to match the bundled PgSTAC image.

## Failure Handling and Idempotency

- Fail the source run for missing or invalid staged inputs, source metadata, geometry-LUT arrays or coordinates, annotation values, or acquisition time.
- Skip a candidate when geometry-LUT selection finds no overlap or a scientific warp is all nodata.
- For an unexpected per-tile processing error, log the failure, retain its identifiable temporary directory for cleanup, continue with remaining candidates, and return a failed run status.
- Stage a leaf product in a sibling temporary directory. Promote it only after nine COGs, the thumbnail, and `item.json` validate.
- Build a complete replacement before replacing an existing valid predecessor.
- Do not register incomplete directories. Leave identifiable temporary directories for cleanup.
- Rebuild the root Catalog from valid leaf products after each source run. A subsequent run repairs stale registration after an interruption.

## Validation and Testing Strategy

### Deterministic tests

- Staged-input validation covers source identity, time, bbox normalization, required assets, four readable local files, provenance sanitization, antimeridian/polar rejection, and no-network behavior.
- MGRS grids have exact bounds, CRS, transform, and `4000 × 4000` shape in both hemispheres. Candidate enumeration covers UTM-zone and latitude-band boundaries using only the source bbox, retaining no more than one boundary column per intersecting zone and rejecting redundant overhang columns.
- Synthetic geometry-LUT tests cover physical-coordinate window selection, padding, clipping, rejection, array alignment, and local-window longitude/latitude interpolation.
- Calibration tests verify physical-coordinate LUT interpolation, axis order, bracketed reads, boundary behavior, and `NaN`-preserving Gamma0 math.
- Raster tests use real temporary files to validate direct warps, nine single-band COGs, the HH/HV/VV 2nd-to-98th-percentile RGB thumbnail, COG layout, CRS, transform, compression, nodata, and quantity/polarization tags.
- STAC tests validate Item geometry fallback, all ten assets, source links, time, projection/raster/SAR metadata, Catalog rebuild, empty results, replacement safety, and recovery after stale registration.
- DPS contract tests validate each tracked CWL, shell wrapper, and public inputs; they assert the declared network and resource requirements, public labels and documentation, staged credential-free local-path processing, and convergence on the same local-file workflow. Release CI also validates OGC metadata, image-tag/version parity, and the deployment request path.

### Scientific validation gates

Before release promotion, compare windowed LUT sampling with the full-frame diagnostic reference for one granule. Valid-pixel Gamma0 differences must remain below `1e-3` after matching calculation and resampling conventions.

Inspect one swath-edge tile and one swath-interior tile against independent map features and the intended spectral dataset. Record positional residuals and the GDAL transformer and bilinear-resampling settings.

## Migration Path

1. Keep `main.py` as the authenticated local staging adapter and native-grid diagnostic while characterizing its calibration results.
2. Establish `src/esa_biomass_gamma0/` with deterministic tests, then extract staged-input validation, MGRS grids, calibration, geometry-LUT, raster, STAC, and workflow helpers.
3. Route `main.py` and the proof-of-concept notebook through shared physical-coordinate calibration helpers without changing their diagnostic responsibilities.
4. Implement the sequential staged-source workflow, nine COG assets, thumbnail, atomic product promotion, and Catalog recovery.
5. Add two thin uv-based MAAP application packages: a staged-files adapter whose CWL permits MAAP File staging while its workflow remains credential-free and local-path-only, and an authenticated Item-ID fetch adapter that materializes job-local files before calling the staged workflow.
6. Update README and operational documentation with both input modes, MAAP secret setup, output contract, empty-Catalog behavior, and scientific acceptance gate.

Existing native GCP COGs remain diagnostics. They must not join the UTM tile collection or analysis-ready temporal stacks.

## Decision Log

| Decision | Choice | Rationale |
|---|---|---|
| Output CRS/grid | Per-tile UTM grid | MGRS supplies standard identifiers and fixed grids for compositing. |
| Tile size and resolution | 100 km MGRS tiles at 25 m | This preserves the 25 m ground-range source sampling on a fixed `4000 × 4000` grid. |
| Item granularity | One Item per source granule × tile | It preserves acquisition provenance while grouping same-grid assets. |
| Scientific assets | Four Beta0, four Gamma0, and one GammaNought COG | Downstream users can inspect source amplitudes and the calibration factor beside Gamma0 without multiband rasters. |
| Display asset | One RGB thumbnail outside the scientific raster contract | It supports browsing without changing the COG data model. |
| Geocoding | Direct geometry-LUT local-window warp per scientific output | `geometry/longitude` and `geometry/latitude` avoid sparse embedded-GCP registration while preserving one controlled interpolation per output. |
| Resampling | Bilinear | The workflow controls a single documented interpolation step. |
| MGRS geometry | `mgrs` round-trip in only bbox-intersecting UTM zones, with one boundary column per zone | `mgrs` owns IDs, zones, hemispheres, and bounds; limiting boundary columns follows HLS-observed selection and prevents redundant overhang products, while geometry-LUT overlap determines coverage without an AOI input. |
| Package boundary | `src/esa_biomass_gamma0/` with one workflow API and CLI | Outer adapters stay thin and do not duplicate processing logic. |
| Runtime | Frozen uv environments from `pyproject.toml` and `uv.lock` | One manifest and lock avoid divergent dependency resolution across both MAAP packages. |
| DPS input modes | Separate hand-maintained staged-files and Item-ID fetch CWLs | Distinct schemas isolate fetch authentication; staged permits MAAP File staging while its scientific workflow remains credential-free and local-path-only. |
| Catalog maintenance | Rebuild from validated leaf products | The workflow recovers complete outputs after a failed Catalog update. |
| Empty source result | Valid empty Catalog | A source can have no geometry-LUT-overlapping candidates while the DPS output contract still requires root STAC files. |
| OGC deployment | GitHub Release validates, builds, and updates the two hand-maintained CWLs | CWL stays reviewable in source control without a generator committing derived files. |

## Open Questions

- Which source STAC fields reliably contain BIOMASS instrument mode, orbit/pass direction, and processing baseline?
- What storage prefix and publication target will host the local Catalog after local processing?

## References

- `AGENTS.md` — project workflow requirements and staged-input guardrails
- `dev-docs/plans/2026-07-31-001-feat-source-package-dps-plan.md` — package and DPS implementation plan
- `main.py` — native-radar diagnostic and local staging reference
- `poc.ipynb` — tiled proof of concept
- `README.md` — package usage and validation status

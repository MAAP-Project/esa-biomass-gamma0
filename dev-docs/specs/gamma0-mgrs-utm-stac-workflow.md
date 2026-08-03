# Spec: BIOMASS Gamma0 MGRS UTM/STAC Workflow

## Context

The current prototype calculates Gamma0 in the BIOMASS L1B radar grid and writes GCP-referenced diagnostic COGs. Those outputs preserve source pixels, but acquisitions do not share a map grid. Consumers must GCP-warp them before temporal compositing or fusion with spectral data.

This workflow produces fixed-grid, 25 m MGRS tile products from one staged source granule. Each accepted `(source granule, MGRS tile)` pair produces four Beta0 COGs, four linear-Gamma0 COGs, one resampled GammaNought COG, and one display-only RGB thumbnail. A STAC Item describes the product. The native radar-grid Gamma0 remains a diagnostic, never a production asset.

## Goals

- Convert HH, HV, VH, and VV Beta0 amplitude data to linear Gamma0 with the paired radiometry LUT.
- Produce nine single-band scientific COGs on the exact 100 km MGRS tile grid for each accepted tile.
- Use the tile's UTM CRS and a deterministic `4000 × 4000` grid at 25 m.
- Create one STAC Item per `(source granule, MGRS tile)` pair, plus a local Catalog and Collection.
- Retain acquisition time, projection details, nodata semantics, processing metadata, and sanitized source provenance.
- Receive a source STAC Item JSON, Beta0 TIFF, radiometry NetCDF, and annotation XML as staged local inputs. The production package makes no network requests.

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
- The input Beta0 TIFF has GCP geolocation. Its GCP transformer maps densified map-space tile boundaries to radar pixels and warped window data to UTM.
- The source STAC bbox filters candidates only. GCP overlap determines whether a tile has coverage.
- Gamma0 is linear intensity: `Beta0_amplitude² × gammaNought`. It is not dB.
- Processing arrays use `NaN` for missing values. Written COGs use `-9999.0` as `float32` nodata.
- A source granule may yield no accepted tiles. That run still writes a valid empty Catalog and Collection.

## Architecture Overview

```text
upstream orchestration: select one BIOMASS L1B source Item
  -> stage source Item JSON + Beta0 TIFF + LUT NetCDF + annotation XML
  -> DPS run (local staged paths only)
       -> validate source metadata and staged files
       -> read Beta0 header and GCPs
       -> find source-bbox candidates
       -> for each source-item × MGRS-tile pair
            -> densify tile boundary; map it to a padded Beta0 pixel window
            -> range-read the four-band Beta0 window
            -> sample only the required physical-coordinate LUT slice
            -> calculate windowed Gamma0
            -> directly warp Beta0, Gamma0, and GammaNought to the tile grid
            -> validate nine single-band COGs and create an RGB thumbnail
            -> write and validate the STAC Item, then atomically promote it
       -> rebuild local STAC Catalog and Collection from valid Item directories
```

The package must not build a virtual stack from radar-geometry data. Consumers may stack only assets already warped to an identical tile grid.

## Processing Design

### 1. Stage one source granule

1. Upstream orchestration selects a `BiomassLevel1b` Item and submits one job for it.
2. The job stages the source Item JSON, `enclosure_tiff` (Beta0), `enclosure_nc` (radiometry LUT), and `enclosure_annot_xml` (annotation) as OGC `File` inputs.
3. The package validates the source ID, acquisition datetime, horizontal bbox, required Item asset entries, and readable regular staged files. It rejects antimeridian and polar bboxes for this UTM-only release.
4. The package retains source ID, collection, time, Item self link, and required asset URLs as provenance after removing URL user info, query parameters, and fragments. It never retain or log signed URLs or credentials.
5. The package opens staged files directly. It performs no token exchange, STAC search, HTTP request, cache management, or download.

For local development, `stage-and-process-gamma0 <item-id>` authenticates and materializes a sanitized source Item plus three granule assets in a persistent cache before delegating to `process-gamma0`; `main.py` reuses that staging path for native-grid diagnostics. The production workflow receives only the four staged local files and does not accept an AOI or study-vector input.

### 2. Select MGRS output tiles

1. Use `mgrs` to enumerate standard 100 km MGRS tiles only in UTM zones intersecting the source bbox. Filter their WGS84 envelopes against the source bbox; do not probe neighboring zones.
2. Derive each retained tile's UTM zone, hemisphere, EPSG code, and exact 100 km bounds through `MGRSToUTM`.
3. Open the staged Beta0 TIFF and obtain its GCPs and GCP CRS.
4. For each candidate, densify the tile perimeter in the tile UTM CRS, transform it to the GCP CRS, then use GDAL's GCP transformer to back-project it to Beta0 pixel coordinates.
5. Pad the resulting radar window, clip it to the source raster, and skip the tile when the result is empty, non-finite, or outside the source coverage.

The source bbox reduces work. The GCP-derived overlap check remains authoritative.

### 3. Read and calibrate a source window

1. Read the accepted four-polarization Beta0 window from the staged TIFF.
2. Shift GCP row and column coordinates into that window's local frame without mutating the original GCPs.
3. Parse the staged annotation XML for the azimuth interval, range-pixel spacing, and ground-to-slant polynomial.
4. Open the staged NetCDF LUT, validate its `(azimuth, range)` coordinate axes, then convert full-source line and sample coordinates for the padded window to physical azimuth and slant-range time.
5. Read a one-cell-bracketed `radiometry/gammaNought` slice and bilinearly sample it onto the Beta0 window. Do not transpose, flip, scale full LUT extents, or interpolate the full LUT for a tile window.
6. Convert Beta0 nodata to `NaN` and calculate Gamma0 for each polarization.

```python
window_gamma0 = window_beta0.astype("float32") ** 2 * window_gamma_nought
```

### 4. Warp to the fixed MGRS grid

Warp each of the four local Beta0 polarizations, four calibrated Gamma0 polarizations, and the resampled GammaNought window directly from shifted GCP geometry to the fixed MGRS grid:

- destination CRS: the tile UTM EPSG code;
- destination bounds: exact `mgrs`-derived tile bounds;
- destination transform: north-up, 25 m pixels aligned to the tile bounds;
- destination shape: `4000 × 4000`;
- resampling: bilinear;
- source nodata: `NaN`;
- destination nodata: `NaN` during computation and `-9999.0` when written.

Each scientific output has one radar-window-to-tile-grid interpolation. The workflow must not write an intermediate GCP COG and warp it later. It must not use cubic or average resampling unless a later scientific validation decision changes this specification.

A partial source footprint leaves nodata in uncovered portions of the complete fixed tile extent.

### 5. Write and validate tile assets

For every accepted pair, stage this product directory beside its final destination:

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
```

The nine GeoTIFFs must be:

- single-band `float32` scientific rasters;
- tiled with 512-pixel blocks and DEFLATE compression;
- valid Cloud Optimized GeoTIFFs;
- north-up with an affine UTM transform, not GCP-only georeferencing;
- assigned the tile EPSG code and `-9999.0` nodata;
- tagged with quantity, units, source Item ID, and processing version; and
- tagged with polarization for Beta0 and Gamma0 assets.

All nine COGs share CRS, bounds, transform, width, height, nodata, and baseline valid-footprint geometry where their source values are valid. Beta0 and Gamma0 use their corresponding quantity and unit metadata. GammaNought is a single-band calibration-factor asset. The PNG thumbnail is display-only, has three RGB bands, and does not replace a scientific asset or claim COG properties.

Validate all COGs and the thumbnail before writing `item.json` and atomically promoting the staged directory.

## STAC Design

### Catalog and Collection

Create a self-contained local Catalog rooted at `<output-root>/catalog.json` and one Collection:

```text
id: biomass-gamma0-mgrs-25m
```

After every run, scan nested output directories for valid Items with their required nine COGs and thumbnail. Rebuild Catalog links and Collection temporal extent from those valid leaf products, and set the Collection spatial extent to their single enclosing WGS84 bbox, then atomically replace root STAC files. This recovers a complete product after an interrupted Catalog update.

When no Item exists, write a valid empty Catalog and Collection with global spatial extent and open temporal extent. Replace those conservative extents when Items exist.

The Collection's standard `item_assets` metadata defines the same nine scientific assets and thumbnail as every Item. Each definition has a readable polarization-specific title, media type, and roles so STAC browsers can distinguish the four Beta0 and four Gamma0 assets. Per-Item projection, raster, and SAR fields remain on the Item assets.

The Collection also uses the Render extension to provide display-only `beta0-rgb` and `gamma0-rgb` HH/HV/VV composites. Their per-channel `rescale` ranges are respectively `[[0.1, 1.0], [0.025, 0.42], [0.12, 0.8]]` and `[[0.005, 0.5], [0.0003, 0.09], [0.007, 0.3]]`, with `-9999.0` nodata. These rounded ranges came from 2nd-to-98th percentile overview samples across the local 636-tile validation set. They are visualization defaults only and do not transform the scientific assets.

Validate Catalog, Collection, and Items with PySTAC before reporting success. Use the Projection, Raster, SAR, and Render extensions where fields are populated. Collection and Item `stac_extensions` list the exact schema URLs used by the installed PySTAC version.

### Item identity and geometry

Create one Item per source granule and MGRS tile:

```text
id: gamma0-<source-item-id>-<mgrs-tile-id>
collection: biomass-gamma0-mgrs-25m
datetime: source acquisition datetime
geometry: intersection of source coverage and tile footprint
bbox: bbox of geometry
```

When a reliable source-coverage polygon cannot be built from the GCP transformer, use full-tile geometry and set `maap:partial_coverage=true`. This fallback affects Item geometry only; the raster grid remains the full tile.

Required Item properties include:

```json
{
  "mgrs:tile": "<100-km tile ID>",
  "datetime": "<source acquisition datetime>",
  "platform": "BIOMASS",
  "instruments": ["P-SAR"],
  "sar:instrument_mode": "<from source when available>",
  "sar:polarizations": ["HH", "HV", "VH", "VV"],
  "processing:level": "Gamma0",
  "maap:processing_version": "<package version>",
  "proj:epsg": "<tile UTM EPSG>",
  "proj:shape": [4000, 4000],
  "proj:transform": [25.0, 0.0, "<xmin>", 0.0, -25.0, "<ymax>"]
}
```

Copy SAR mode, orbit, pass direction, and processing-baseline fields only when the source provides them. `mgrs:*`, `maap:*`, and `processing:*` remain project conventions until the project adopts corresponding STAC extensions; document them in Collection descriptions and summaries.

Add a `derived_from` link to the sanitized input Item self HREF when available. Add sanitized `via` links to the Beta0 TIFF and radiometry NetCDF source URLs.

### Assets

Each Item has nine scientific COG data assets and one display-only thumbnail:

| Asset key | Filename | Quantity | Polarization | Roles |
|---|---|---|---|---|
| `beta0_hh` | `beta0_hh.tif` | Beta0 amplitude | HH | `data`, `beta0` |
| `beta0_hv` | `beta0_hv.tif` | Beta0 amplitude | HV | `data`, `beta0` |
| `beta0_vh` | `beta0_vh.tif` | Beta0 amplitude | VH | `data`, `beta0` |
| `beta0_vv` | `beta0_vv.tif` | Beta0 amplitude | VV | `data`, `beta0` |
| `gamma0_hh` | `gamma0_hh.tif` | linear Gamma0 intensity | HH | `data`, `gamma0` |
| `gamma0_hv` | `gamma0_hv.tif` | linear Gamma0 intensity | HV | `data`, `gamma0` |
| `gamma0_vh` | `gamma0_vh.tif` | linear Gamma0 intensity | VH | `data`, `gamma0` |
| `gamma0_vv` | `gamma0_vv.tif` | linear Gamma0 intensity | VV | `data`, `gamma0` |
| `gamma_nought` | `gamma_nought.tif` | GammaNought calibration factor | n/a | `data`, `calibration` |
| `thumbnail` | `thumbnail.png` | display-only RGB composite | n/a | `thumbnail`, `overview` |

Each scientific asset includes its media type, Projection extension fields, a one-band Raster extension entry, and SAR polarization metadata where applicable. Gamma0 raster-band metadata uses `float32`, `-9999.0` nodata, unit `1`, and a linear-intensity description. The thumbnail has image media type and RGB metadata but no scientific Raster or SAR claims.

## Package and Runtime Interface

The installable package lives under `src/esa_biomass_gamma0/`. It owns one staged-source workflow API and an `argparse` CLI. Root `run.py`, `run.sh`, CWL, the notebook, and diagnostic adapters call package code instead of duplicating processing logic.

The production entry point handles one staged source granule:

```text
process-gamma0 \
  --source-item path/to/source-item.json \
  --beta0-tiff path/to/enclosure.tif \
  --radiometry-lut path/to/enclosure.nc \
  --annotation-xml path/to/annotation.xml \
  --output-root output/ \
  --resolution 25 \
  --overwrite
```

| Setting | Meaning |
|---|---|
| `source_item` | Staged source STAC Item JSON with identity, time, bbox, and provenance |
| `beta0_tiff` | Staged `enclosure_tiff` file |
| `radiometry_lut` | Staged `enclosure_nc` file |
| `annotation_xml` | Staged `enclosure_annot_xml` file |
| `output_root` | Output directory, fixed to `./output` by the DPS wrapper |
| `resolution` | Fixed at `25` for this release |
| `overwrite` | Rebuild an otherwise complete tile product |
| `window_padding_pixels` | Internal radar-window safety margin for scientific tuning |
| `processing_version` | Installed package version stored in COG and STAC metadata |

`algorithm.yml` and CWL expose the four staged inputs as `File` values with matching defaults for `resolution` and `overwrite`. `run.sh`, `run.py`, and the `process-gamma0` package CLI accept the same local path names; the DPS wrapper fixes `output_root` to `./output`. CWL disables network access and returns `./output` as a `Directory`.

`build.sh` installs the frozen production environment with `uv sync --frozen --no-dev`. `run.sh` creates `./output` and forwards staged paths through `uv run --frozen --no-dev`. `stage-and-process-gamma0` is a development-only command that uses the authenticated staging dependencies before calling the same production CLI. `pyproject.toml` and `uv.lock` remain the only dependency definition and lock. Production dependencies belong in `[project.dependencies]`; notebook, authenticated-staging, plotting, and test dependencies belong in development groups where possible.

### Local eoAPI integration

The repository's development-only `docker-compose.yml` runs PgSTAC, STAC API,
TiTiler, and STAC Browser on the standard eoAPI ports. It mounts the local
output root read-only into TiTiler at `/data/gamma0`; `GAMMA0_OUTPUT_ROOT`
overrides the default `./output` host path.

`scripts/load_pgstac.py` loads the root Collection and the Item links it
registers. At load time only, it maps each relative local asset HREF to a
`file:///data/gamma0/...` URI. The generated Item, Collection, and Catalog
files retain relative asset HREFs and remain portable outside Docker. Use the
`pypgstac[psycopg]==0.9.11` extra to match the bundled PgSTAC image.

## Failure Handling and Idempotency

- Fail the source run for missing or invalid staged inputs, source metadata, GCPs, LUT coordinates, annotation values, or acquisition time.
- Skip and log a candidate when GCP back-projection finds no overlap or a scientific warp is all nodata.
- Stage a leaf product in a sibling temporary directory. Promote it only after nine COGs, the thumbnail, and `item.json` validate.
- Treat an existing valid Item with all required local assets as complete unless `--overwrite` is set.
- Build an overwrite replacement before replacing a valid predecessor.
- Do not register incomplete directories. Leave identifiable temporary directories for cleanup.
- Rebuild root STAC files from valid leaf products after each source run. A subsequent run repairs stale registration after an interruption.

## Validation and Testing Strategy

### Deterministic tests

- Staged-input validation covers source identity, time, bbox normalization, required assets, four readable local files, provenance sanitization, antimeridian/polar rejection, and no-network behavior.
- MGRS grids have exact bounds, CRS, transform, and `4000 × 4000` shape in both hemispheres. Candidate enumeration covers UTM-zone and latitude-band boundaries using only the source bbox.
- Synthetic GCP tests cover boundary densification, acceptance, rejection, padding, clipping, non-finite mappings, and local-GCP shifting.
- Calibration tests verify physical-coordinate LUT interpolation, axis order, bracketed reads, boundary behavior, and `NaN`-preserving Gamma0 math.
- Raster tests use real temporary files to validate direct warps, nine single-band COGs, thumbnail structure, COG layout, CRS, transform, compression, nodata, and quantity/polarization tags.
- STAC tests validate Item geometry fallback, all ten assets, source links, time, projection/raster/SAR metadata, Catalog rebuild, empty results, idempotency, overwrite safety, and recovery after stale registration.
- DPS contract tests validate input and default parity across algorithm metadata, CWL, shell wrappers, and CLI, and ensure CWL has no network or credential configuration.

### Scientific validation gates

Before release promotion, compare windowed LUT sampling with the full-frame diagnostic reference for one granule. Valid-pixel Gamma0 differences must remain below `1e-3` after matching calculation and resampling conventions.

Inspect one swath-edge tile and one swath-interior tile against independent map features and the intended spectral dataset. Record positional residuals and the GDAL transformer and bilinear-resampling settings.

## Migration Path

1. Keep `main.py` as the authenticated local staging adapter and native-grid diagnostic while characterizing its calibration results.
2. Establish `src/esa_biomass_gamma0/` with deterministic tests, then extract staged-input validation, grid/GCP, calibration, raster, STAC, and workflow helpers.
3. Route `main.py` and the proof-of-concept notebook through shared physical-coordinate calibration helpers without changing their diagnostic responsibilities.
4. Implement the sequential staged-source workflow, nine COG assets, thumbnail, atomic product promotion, and Catalog recovery.
5. Add `run.py`, `run.sh`, CWL, `algorithm.yml`, and `build.sh` as thin uv-based MAAP adapters.
6. Update README and operational documentation with the package layout, staged inputs, output contract, empty-Catalog behavior, and scientific acceptance gate.

Existing native GCP COGs remain diagnostics. They must not join the UTM tile collection or analysis-ready temporal stacks.

## Decision Log

| Decision | Choice | Rationale |
|---|---|---|
| Output CRS/grid | Per-tile UTM grid | MGRS supplies standard identifiers and fixed grids for compositing. |
| Tile size and resolution | 100 km MGRS tiles at 25 m | This preserves the 25 m ground-range source sampling on a fixed `4000 × 4000` grid. |
| Item granularity | One Item per source granule × tile | It preserves acquisition provenance while grouping same-grid assets. |
| Scientific assets | Four Beta0, four Gamma0, and one GammaNought COG | Downstream users can inspect source amplitudes and the calibration factor beside Gamma0 without multiband rasters. |
| Display asset | One RGB thumbnail outside the scientific raster contract | It supports browsing without changing the COG data model. |
| Geocoding | Direct local-window warp per scientific output | This avoids intermediate GCP COGs and gives each output one controlled interpolation. |
| Resampling | Bilinear | The workflow controls a single documented interpolation step. |
| MGRS geometry | `mgrs` round-trip in only bbox-intersecting UTM zones | `mgrs` owns IDs, zones, hemispheres, and bounds; restricting zones prevents duplicate geographic coverage, while GCP overlap determines coverage without an AOI input. |
| Package boundary | `src/esa_biomass_gamma0/` with one workflow API and CLI | Outer adapters stay thin and do not duplicate processing logic. |
| Runtime | Frozen uv environment from `pyproject.toml` and `uv.lock` | One manifest and lock avoid divergent dependency resolution. |
| Catalog maintenance | Rebuild from validated leaf products | The workflow recovers complete outputs after a failed Catalog update. |
| Empty source result | Valid empty Catalog and Collection | A source can have no GCP-overlapping candidates while the DPS output contract still requires root STAC files. |

## Open Questions

- Which source STAC fields reliably contain BIOMASS instrument mode, orbit/pass direction, and processing baseline?
- Which documented Gamma0 polarization mapping, scaling, and image encoding should produce `thumbnail.png`?
- What storage prefix and publication target will host the local Catalog after local processing?

## References

- `AGENTS.md` — project workflow requirements and staged-input guardrails
- `dev-docs/plans/2026-07-31-001-feat-source-package-dps-plan.md` — package and DPS implementation plan
- `main.py` — native-GCP diagnostic and local staging reference
- `poc.ipynb` — tiled proof of concept
- `README.md` — package usage and validation status

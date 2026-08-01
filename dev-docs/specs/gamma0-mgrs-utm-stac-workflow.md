# Spec: BIOMASS Gamma0 MGRS UTM/STAC Workflow

## Context

The current prototype calculates Gamma0 in the BIOMASS L1B radar grid and writes GCP-referenced diagnostic COGs. That preserves source pixels, but separate acquisitions do not share a map grid. Consequently, direct temporal compositing and fusion with spectral data require every consumer to make its own GCP warp.

This workflow produces analysis-ready Gamma0 outputs on fixed 100 m grids for every standard 100 km MGRS tile intersecting a source granule. Each output is a single-band COG and is described by a STAC Item. The native radar-grid Gamma0 remains an in-process intermediate, not the analysis product.

## Goals

- Convert each selected BIOMASS L1B granule's HH, HV, VH, and VV Beta0 amplitude data to linear Gamma0 using the paired radiometry LUT.
- Produce one set of four single-band, 100 m COGs for every intersecting 100 km MGRS tile.
- Use the tile's native UTM CRS and a deterministic, tile-aligned 1,000 × 1,000 pixel grid.
- Create one STAC Item per `(source granule, MGRS tile)` with one Gamma0 polarization per asset.
- Retain source provenance, acquisition time, projection details, nodata semantics, and processing metadata.
- Receive the complete Beta0 TIFF, radiometry LUT, annotation XML, and source STAC Item JSON as staged local inputs before reading source windows. The package does not download source assets.

## Non-goals

- Create temporal composites or biomass-model features. Those consume these tile products.
- Mosaic overlapping granules.
- Create a global equal-area output grid.
- Publish a remote STAC API or manage credentials for publication.
- Retain a native-grid COG for every production tile. Native-grid diagnostics may be explicitly enabled for validation only.

## Constraints and Assumptions

- Output resolution is exactly 100 m.
- A 100 km MGRS tile is represented by its `mgrs`-derived UTM CRS and exact 100,000 m square extent, so the output grid has shape `[1000, 1000]`.
- Use the `mgrs` package to enumerate standard 100 km tiles and derive their UTM zone, hemisphere, and bounds. Do not parse tile IDs or create an approximate fishnet.
- The input Beta0 uses GCP geolocation. GCPs are sufficient to map a densified map-space ROI boundary into radar pixel coordinates and to warp the Gamma0 window into UTM.
- Gamma0 is linear intensity: `Beta0_amplitude² × gammaNought`. It is not dB.
- Internal missing values are `NaN`; final COG nodata is `-9999.0` (`float32`).
- One source granule can yield zero tile Items when it has no GCP overlap with a candidate MGRS tile.

## Architecture Overview

```text
upstream orchestration: study AOI + date range
  -> MAAP STAC search for BIOMASS L1B source Items
  -> stage one source Item JSON + Beta0 TIFF + LUT NetCDF + annotation XML
  -> DPS run (local staged paths only)
       -> read Beta0 header and GCPs
       -> find intersecting standard MGRS tile footprints
       -> for each source-item × MGRS-tile pair
            -> densify tile boundary; map it to a padded Beta0 pixel window
            -> range-read that four-band Beta0 window
            -> read only the corresponding LUT portion onto the Beta0 window
            -> calculate windowed HH/HV/VH/VV Gamma0
            -> one GCP-based warp per polarization to the fixed tile UTM grid
            -> write four single-band COGs
            -> write STAC Item JSON referencing those COGs
  -> local STAC Catalog/Collection JSON
```

The workflow must not make a virtual stack from raw radar-geometry data. Stacking is permitted only after all source windows have been warped to an identical target tile grid.

## Processing Design

### 1. Stage one source granule

1. Upstream orchestration searches MAAP STAC using its selected study area and date interval, then submits one job for every selected `BiomassLevel1b` Item.
2. Stage the source STAC Item JSON, `enclosure_tiff` (Beta0), `enclosure_nc` (radiometry LUT), and `enclosure_annot_xml` (annotation) as OGC `File` inputs.
3. The package validates required Item asset entries, source ID, acquisition datetime, bbox, and readable staged files. It retains the Item JSON/self link and original asset URLs as provenance.
4. The package opens the staged Beta0 TIFF directly. It performs no token exchange, STAC search, HTTP request, or cache management.

For local development only, a staging adapter may reuse `main.py`'s authenticated `obstore` cache/download helpers to materialize these four inputs before calling the production path.

### 2. Select MGRS output tiles

1. Use `mgrs` to enumerate standard 100 km MGRS tiles in UTM zones intersecting the source STAC bounds, then filter their WGS84 envelopes against those bounds.
2. Derive each candidate's UTM zone, hemisphere, and exact 100 km bounds through `MGRSToUTM`.
3. Open the staged local Beta0 TIFF and obtain its GCPs and GCP CRS.
4. For each candidate MGRS tile, densify the tile perimeter in the tile UTM CRS, transform it to the GCP CRS, and back-project it to Beta0 pixel coordinates using GDAL's GCP transformer.
5. Add configurable radar-pixel padding, clip to the source raster, and reject an empty or non-overlapping window.

The source STAC footprint is only a cheap candidate filter. The GCP-derived overlap check is authoritative because it reflects the actual radar raster coverage.

### 3. Read and calibrate a source window

1. Read the accepted Beta0 pixel window for all four polarizations from the staged local TIFF.
2. Shift the Beta0 GCP pixel row/column coordinates into the local window coordinate system; retain the original GCP coordinates separately for provenance/debugging.
3. Open the staged local NetCDF LUT and annotation XML.
4. Read `radiometry/gammaNought` plus its coordinate vectors and product annotation values needed to map Beta0 line/sample coordinates to LUT azimuth time and slant-range time.
5. Determine the LUT region needed for the padded Beta0 window. Bilinearly sample that LUT region directly onto the Beta0 window. Do not interpolate the full LUT or scale array extents as a substitute for physical coordinate mapping.
6. Convert input nodata to `NaN`, then calculate Gamma0 for each polarization.

```python
window_gamma0 = window_beta0.astype("float32") ** 2 * window_gamma_nought
```

### 4. Warp to the fixed MGRS grid

For each polarization, warp the Gamma0 window directly from its shifted GCP geometry to the fixed target grid:

- destination CRS: the MGRS tile UTM EPSG code
- destination bounds: exact `mgrs`-derived tile bounds in destination CRS
- destination transform: north-up, 100 m pixels, aligned to the tile bounds
- destination shape: `1000 × 1000`
- resampling: bilinear
- source nodata: `NaN`
- destination nodata: `NaN` during computation, `-9999.0` when written

There is exactly one interpolation between calibrated radar pixels and the analysis grid. Do not first write a GCP COG and then warp that COG. Do not use cubic or average resampling unless a later validation decision changes this spec.

A tile output may contain nodata where the source granule only partly covers the tile. Its COG nevertheless retains the complete fixed tile extent and grid, allowing straightforward alignment with other dates and spectral products.

### 5. Write COG assets

For each `(source item, MGRS tile)` pair, write four assets:

```text
<output-root>/<mgrs-tile>/<acquisition-date>/<source-item-id>/
  gamma0_hh.tif
  gamma0_hv.tif
  gamma0_vh.tif
  gamma0_vv.tif
  item.json
```

Each GeoTIFF must be:

- one-band `float32`, linear Gamma0 intensity;
- tiled with 512-pixel blocks;
- DEFLATE compressed;
- a valid Cloud Optimized GeoTIFF;
- north-up with an affine UTM transform, not GCP-only georeferencing;
- assigned `EPSG:<tile UTM EPSG>`;
- `-9999.0` nodata;
- tagged with `QUANTITY=gamma0`, `POLARISATION`, `UNITS=linear`, source item ID, and processing version.

The four assets for an Item must have byte-identical projection metadata, bounds, transform, shape, nodata, and valid-data mask geometry aside from any polarization-specific invalid values.

## STAC Design

### Catalog and collection

Create a self-contained local STAC Catalog rooted at `<output-root>/catalog.json` and one Collection:

```text
id: biomass-gamma0-mgrs-100m
```

The Collection has spatial extent covering all emitted Item bboxes and temporal extent covering emitted acquisition datetimes. Update these extents when adding Items. Validate Catalog, Collection, and Items with PySTAC before treating a run as successful.

Use these extensions on Items and assets where fields are populated:

- STAC Projection Extension
- STAC Raster Extension
- STAC SAR Extension

The Collection and Item `stac_extensions` must list the exact extension schema URLs used by the installed PySTAC version.

### Item identity and geometry

Create one Item per source granule and MGRS tile:

```text
id: gamma0-<source-item-id>-<mgrs-tile-id>
collection: biomass-gamma0-mgrs-100m
datetime: source acquisition datetime
geometry: intersection of source coverage and tile footprint
bbox: bbox of geometry
```

If the reliable source-coverage polygon is unavailable from the GCP transformer, use the MGRS tile geometry for `geometry` and set `maap:partial_coverage=true`; this is a conservative metadata fallback only. The raster's fixed grid remains the full tile.

Required Item properties:

```json
{
  "mgrs:tile": "<100-km tile ID>",
  "datetime": "<source acquisition datetime>",
  "platform": "BIOMASS",
  "instruments": ["P-SAR"],
  "sar:instrument_mode": "<from source when available>",
  "sar:polarizations": ["HH", "HV", "VH", "VV"],
  "processing:level": "Gamma0",
  "maap:source_item_id": "<source item ID>",
  "maap:source_collection": "BiomassLevel1b",
  "maap:processing_version": "<package/git version>",
  "proj:epsg": "<tile UTM EPSG>",
  "proj:shape": [1000, 1000],
  "proj:transform": [100.0, 0.0, "<xmin>", 0.0, -100.0, "<ymax>"]
}
```

`mgrs:*`, `maap:*`, and `processing:*` fields are project conventions until a corresponding STAC extension is adopted; document them in the Collection summaries and descriptions.

### Gamma0 assets

Each Item has four data assets, never a multiband Gamma0 asset:

| Asset key | Filename | Polarization | Required roles |
|---|---|---|---|
| `gamma0_hh` | `gamma0_hh.tif` | `HH` | `data`, `gamma0` |
| `gamma0_hv` | `gamma0_hv.tif` | `HV` | `data`, `gamma0` |
| `gamma0_vh` | `gamma0_vh.tif` | `VH` | `data`, `gamma0` |
| `gamma0_vv` | `gamma0_vv.tif` | `VV` | `data`, `gamma0` |

Every asset includes:

```json
{
  "type": "image/tiff; application=geotiff; profile=cloud-optimized",
  "title": "Gamma0 <polarization>, linear intensity",
  "roles": ["data", "gamma0"],
  "proj:epsg": "<tile UTM EPSG>",
  "proj:shape": [1000, 1000],
  "proj:transform": [100.0, 0.0, "<xmin>", 0.0, -100.0, "<ymax>"],
  "raster:bands": [{
    "data_type": "float32",
    "nodata": -9999.0,
    "unit": "1",
    "description": "Gamma0 linear intensity (<polarization>)"
  }],
  "sar:polarizations": ["<polarization>"]
}
```

Add a `source` link from every output Item to the input L1B STAC Item self link when available. Add `derived_from` links to the paired Beta0 TIFF and radiometry NetCDF URLs, without embedding credentials or tokens.

## Interfaces and Configuration

The production entry point accepts one staged source granule. Upstream orchestration owns date-range discovery and creates one DPS job per selected Item:

```text
process-gamma0 \
  --source-item path/to/source-item.json \
  --beta0-tiff path/to/enclosure.tif \
  --radiometry-lut path/to/enclosure.nc \
  --annotation-xml path/to/annotation.xml \
  --study-tiles path/to/boreal_tiles.gpkg \
  --output-root output/ \
  --resolution 100
```

Required configuration:

| Setting | Meaning |
|---|---|
| `source_item` | Staged source STAC Item JSON, retaining ID, time, bbox, and provenance |
| `beta0_tiff` | Staged `enclosure_tiff` local file |
| `radiometry_lut` | Staged `enclosure_nc` local file |
| `annotation_xml` | Staged `enclosure_annot_xml` local file |
| `study_tiles_path` | Boreal AOI/tile index used to constrain tile processing |
| `output_root` | Package output directory (`./output` in the DPS runtime) |
| `resolution_m` | Fixed at `100` for this product version |
| `window_padding_pixels` | Radar-window safety margin around a back-projected tile boundary |
| `processing_version` | Code/package or git version stored in COG/STAC metadata |

`algorithm.yml` and the CWL wrapper expose the first four settings as required
OGC `File` inputs and stage them before `run.py` starts. The production package
has no MAAP credentials. Credentials used upstream or in a local test staging
adapter must never enter config files, output metadata, logs, or STAC JSON.

## Failure Handling and Idempotency

- Skip and log a tile when GCP back-projection finds no overlap.
- Fail the source-item run when required staged inputs, Item metadata, GCPs, LUT coordinates, or acquisition time are missing.
- Fail a tile when its warp cannot establish the fixed destination grid or when all four outputs are entirely nodata.
- Write assets to temporary paths and atomically promote them only after COG validation succeeds.
- Write `item.json` only after all four COGs validate.
- Treat an existing valid `item.json` plus its four valid assets as complete and skip it unless `--overwrite` is set.
- Leave incomplete temporary outputs identifiable for cleanup; do not register them in STAC.

## Validation and Testing Strategy

### Unit tests

- Target-grid construction yields the exact expected UTM CRS, 100 m transform, bounds, and `1000 × 1000` shape for representative northern and southern hemisphere MGRS tiles.
- Candidate-tile selection and GCP boundary back-projection correctly accept, reject, clip, pad, and shift windows using synthetic GCPs.
- LUT coordinate interpolation samples expected values at window edges and handles nodata as `NaN`.
- Gamma0 conversion preserves `NaN` and computes expected linear values.
- Single-band COG writer sets CRS, transform, nodata, band count, compression, and polarization metadata correctly.
- STAC construction has four named assets, source links, source datetime, tile metadata, and projection/raster/SAR fields.

### Integration tests

- Stage one MAAP-backed granule, then process it over a small set of intersecting tiles.
- Confirm that all four assets for every Item open as COGs and have identical target-grid metadata.
- Confirm that two different granules written for the same MGRS tile have identical CRS, transform, width, and height.
- Confirm that a spectral raster reprojected or clipped to the same tile grid aligns without an additional Gamma0 warp.
- Validate emitted STAC with PySTAC and a STAC validator that supports the selected extension versions.

### Scientific validation gates

Before scaling, compare the windowed-LUT workflow against the full-frame reference for one granule. The maximum Gamma0 difference over valid pixels must remain below `1e-3` after matching calculation and resampling conventions.

For at least one tile near a swath edge and one in the swath interior, inspect GCP-warped Gamma0 against independent map features and the intended spectral dataset. Record positional residuals and the GDAL transformer/resampling settings used.

## Migration Path

1. Keep `main.py` and its native GCP COGs as the diagnostic reference while correcting and validating physical-coordinate LUT alignment.
2. Extract reusable helpers for staged-input validation, GCP-window calculation, LUT sampling, Gamma0 calculation, fixed-grid warp, COG writing, and STAC serialization.
3. Add a one-staged-granule, one-tile integration path and validate it against the native/full-frame reference.
4. Add candidate MGRS-tile discovery and process every intersecting tile for a staged source Item.
5. Emit local Catalog/Collection/Item metadata after COG validation.
6. Add the DPS OGC Application Package wrappers with four staged source `File` inputs; retain `main.py` download helpers only for local test staging.
7. Deprecate production use of native-grid `gamma0.tif`; retain it only behind an explicit diagnostic flag.

Existing native GCP COG outputs are not backward-compatible analysis assets. They remain valid diagnostics but must not be mixed with the new UTM tile collection.

## Decision Log

| Decision | Options considered | Rationale |
|---|---|---|
| Output CRS/grid | GCP-only radar grid; one project-wide Albers grid; per-tile UTM grid | Per-tile UTM is requested, maps naturally to MGRS tiles, and creates fixed grids for compositing. |
| Tile size and resolution | Arbitrary AOI chips; 100 km MGRS tiles at 100 m | Standard tile identifiers aid downstream joins; fixed extent gives 1,000 × 1,000 outputs. |
| Item granularity | One Item per granule; one per tile/date; one per composite | One Item per granule × tile preserves acquisition provenance while keeping all same-grid bands together. |
| Gamma0 packaging | Four-band COG; four single-band COGs | Four assets allow polarization-specific access and meet the requested STAC layout. |
| Geocoding point | Preserve GCPs in output; warp at end; warp Beta0 before calibration | Warp once after radar-space LUT calibration avoids unnecessary interpolation and yields an analysis-ready affine grid. |
| Resampling | Nearest; bilinear; cubic | Bilinear is the established workflow choice and has one controlled interpolation step. |
| MGRS geometry | Generate from tile IDs; authoritative tile index | `mgrs` derives standard tile IDs, UTM zones, hemispheres, and exact bounds without a separately maintained index. |

## Open Questions

- Which source STAC fields reliably contain BIOMASS platform, instrument mode, orbit/pass direction, and processing baseline, so they can be copied instead of guessed?
- Should the final Collection include optional quicklook assets or statistics assets, or are four Gamma0 data assets sufficient for the first release?
- What storage prefix and publication target will host the local Catalog once the product moves beyond local processing?

## References

- `AGENTS.md` — project workflow requirements and staged-input guardrails
- `main.py` — current native-GCP diagnostic implementation
- `README.md` — Gamma0/LUT alignment requirements
- `above_gamma0/Gamma0_workflow_optimized.ipynb` — reference workflow
- `above_gamma0/BL1_Gamma0.py` — existing Gamma0 calculation helpers

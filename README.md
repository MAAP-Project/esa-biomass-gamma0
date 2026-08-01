# ESA BIOMASS Gamma0 MGRS DPS

A MAAP DPS/OGC Application Package in development for staged ESA BIOMASS
Level-1B products. Each accepted source-granule × 100 km MGRS-tile pair will
produce four Beta0 COGs, four linear-Gamma0 COGs, one resampled GammaNought
COG, one display-only RGB thumbnail, and a STAC Item on a fixed native-UTM grid.

[`dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md`](dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md)
defines the product contract. The active package plan lives in
[`dev-docs/plans/2026-07-31-001-feat-source-package-dps-plan.md`](dev-docs/plans/2026-07-31-001-feat-source-package-dps-plan.md).

## Status

The repository is moving from a calibration diagnostic to the production tile
product:

- `main.py` is the current diagnostic reference. It can download one L1B Item
  for local testing, align the radiometry LUT in radar geometry, calculate
  Gamma0, and write native-grid GCP-referenced COGs.
- `src/esa_biomass_gamma0/` is installable and provides local-only
  staged-source validation, MGRS target-grid/GCP-window helpers, and windowed
  physical-coordinate calibration. `main.py` and the notebook share the
  calibration helpers. The package accepts a source Item JSON, Beta0 TIFF,
  radiometry LUT NetCDF, and annotation XML as local paths; it does not retrieve
  source assets.
- The remaining workflow work is direct fixed-UTM warps, COG/STAC output, and
  the DPS wrapper. Native-GCP diagnostics are not
  analysis-ready tile products and must not join the production collection.

## Product contract

For each accepted source Item and intersecting standard 100 km MGRS tile, the
production package will:

1. Validate four staged local inputs. The source Item supplies identity,
   acquisition time, bbox, and sanitized provenance.
2. Use the source bbox only to filter MGRS candidates. Densified tile-boundary
   back-projection through the Beta0 GCP transformer
   determines coverage.
3. Read a padded local Beta0 window, sample only the required LUT region in
   radar coordinates, and calculate `Gamma0 = Beta0_amplitude² × gammaNought`.
4. Warp four Beta0 polarizations, four Gamma0 polarizations, and resampled
   GammaNought directly to the tile's exact north-up `4000 × 4000`, 25 m UTM
   grid. Each scientific output gets one bilinear interpolation.
5. Validate nine single-band `float32` COGs and an RGB thumbnail, write the
   STAC Item, atomically promote the product, then rebuild local STAC metadata
   from valid nested Items.

Source discovery, authentication, and remote asset retrieval happen upstream.
An orchestrator can search MAAP STAC and submit one staged input set per L1B
Item. The production package and CWL runtime have no network access or MAAP
credentials.

### Output layout

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

Scientific COGs use `NaN` during calculation, `-9999.0` final nodata, DEFLATE
compression, 512-pixel blocks, and the MGRS-derived UTM CRS and affine transform.
Gamma0 assets contain linear intensity, never dB. The thumbnail is display-only;
it does not replace a scientific asset.

Items use `gamma0-<source-item-id>-<mgrs-tile-id>` in the
`biomass-gamma0-mgrs-25m` Collection. They retain source, projection, raster,
SAR, MGRS, and processing provenance metadata. A source with no accepted tile
still produces a valid empty Catalog and Collection.

## Planned package layout

```text
algorithm.yml                    # MAAP algorithm metadata, resources, and inputs
build.sh                          # install frozen uv production environment
esa-biomass-gamma0.cwl           # OGC Application Package / CWL wrapper
run.sh                            # creates ./output and forwards staged paths
run.py                            # thin package CLI adapter; no processing logic
src/esa_biomass_gamma0/          # source validation, grids, calibration, raster, STAC, workflow
tests/                            # deterministic synthetic tests
main.py                           # retained native-grid diagnostic reference
```

The package owns the processing workflow. `run.py`, `run.sh`, CWL, notebooks,
and `main.py` adapt inputs or provide diagnostics; they do not copy production
processing logic.

## DPS interface

| Input | Type | Meaning |
| --- | --- | --- |
| `source_item` | File | Staged source STAC Item JSON. |
| `beta0_tiff` | File | Staged `enclosure_tiff` asset. |
| `radiometry_lut` | File | Staged `enclosure_nc` asset. |
| `annotation_xml` | File | Staged `enclosure_annot_xml` asset. |
| `resolution` | number | Resolution in metres. Version 1 accepts only `25`. |
| `overwrite` | boolean | Rebuild a valid existing source-Item × tile product. |

`algorithm.yml`, CWL, shell wrappers, and the CLI use these names and matching
defaults. The four paths stage as CWL `File` values. CWL returns `./output` as a
`Directory`.

## Development

The project uses `uv`. `pyproject.toml` and `uv.lock` provide the only runtime
dependency definition and lock. The future DPS build uses:

```bash
uv sync --frozen --no-dev
```

The current diagnostic can materialize local test inputs with `main.py`'s
`fetch_assets`, `cache_paths`, and `write_cached_asset` helpers. Supply MAAP
credentials outside version control:

```bash
export ESA_MAAP_CLIENT_SECRET=...
export ESA_OFFLINE_TOKEN=...
uv run python main.py '<stac-item-id>' --out-dir diagnostics
```

The diagnostic caches assets in `/tmp` by default; pass `--cache-dir` to use a
persistent cache. It writes `beta0.tif`, `lut_native.tif`, `lut_resampled.tif`,
and `gamma0.tif` in native GCP-referenced radar geometry. Those files serve
scientific validation and are not production tile products.

The production runner receives staged files and needs no MAAP credentials. Do
not place credentials or signed source URLs in metadata, logs, STAC JSON, or
committed configuration.

## Non-negotiable processing rules

- Derive MGRS IDs, UTM zones, and exact bounds through `mgrs`; do not parse IDs,
  use a hard-coded fishnet, or rely on source-bbox approximations for tile extents.
- Calibrate in radar geometry before geocoding. A later GCP warp cannot repair
  a LUT sampled at incorrect radar coordinates.
- Preserve LUT axis order `(azimuth, range)`; do not transpose, flip, or stretch
  the complete LUT across Beta0 pixels.
- Do not write an intermediate GCP COG. Each scientific output gets one direct
  bilinear warp to its fixed target grid.
- Do not combine products from different UTM CRSs before each reaches its fixed
  tile grid.
- Validate nine COGs and the thumbnail before registering `item.json`. A valid
  Item with all required assets is complete unless `--overwrite` is set.

## Implementation milestones

1. Establish the installable `src/esa_biomass_gamma0` package and deterministic
   test foundation.
2. Extract shared staged-input, physical-coordinate calibration, MGRS-grid, and
   GCP-window helpers from the diagnostic and proof-of-concept paths.
3. Build one sequential staged-source workflow with nine fixed-grid COGs, an RGB
   thumbnail, atomic product promotion, and recoverable local STAC metadata.
4. Add the uv-based DPS package files and validate the no-network CWL contract.
5. Run MAAP-backed Gamma0 and positional validation before release promotion.

See [`AGENTS.md`](AGENTS.md) for contributor guardrails and the workflow
specification for detailed validation gates and open questions.

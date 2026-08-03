# ESA BIOMASS Gamma0 MGRS DPS

Create fixed-grid 25 m MGRS Beta0 and linear Gamma0 products from a staged ESA
BIOMASS Level-1B granule.

## About

This MAAP DPS algorithm converts the four-polarization Beta0 amplitudes in one
ESA BIOMASS L1B product into analysis-ready products on every overlapping
standard 100 km MGRS tile in a UTM zone intersecting the source bbox. For each
tile, it:

1. Reads the staged source STAC Item, Beta0 TIFF, radiometry LUT, and annotation XML.
2. Samples the GammaNought LUT in radar geometry and calculates linear Gamma0
   as `Beta0² × GammaNought`.
3. Warps four Beta0 polarizations, four Gamma0 polarizations, and GammaNought
   directly onto an exact 4,000 × 4,000 pixel, 25 m UTM tile grid.
4. Writes nine Cloud Optimized GeoTIFFs, a display-only RGB thumbnail, and STAC
   metadata.

Source discovery and download happen before the job is submitted. The DPS
stages the four file inputs; the algorithm does not authenticate, search STAC,
or make network requests.

## Usage

Submit one job for each source granule after staging its STAC Item and required
assets somewhere the DPS can read, such as your MAAP workspace S3 bucket.
Assuming the algorithm is registered as `esa_biomass_gamma0` version `v0.1.0`:

```python
from maap.maap import MAAP

maap = MAAP()
job = maap.submitJob(
    identifier="biomass-gamma0-example",
    algo_id="esa_biomass_gamma0",
    version="v0.1.0",
    queue="maap-dps-worker-8gb",
    source_item="s3://<bucket>/source-item.json",
    beta0_tiff="s3://<bucket>/enclosure.tif",
    radiometry_lut="s3://<bucket>/enclosure.nc",
    annotation_xml="s3://<bucket>/annotation.xml",
    resolution=25,
    overwrite=False,
)
print(job.id)
```

Use a queue available to your MAAP organization that meets the algorithm's 8 GB
memory and four-core requirement. The job runs asynchronously; use the MAAP
Jobs UI or `job.retrieve_attributes()` to monitor it.

### Local CLI

For local development, provide the same four inputs as local files. The CLI
creates the output directory and does not download source assets:

```bash
uv sync --frozen
uv run --frozen --no-dev process-gamma0 \
  --source-item path/to/source-item.json \
  --beta0-tiff path/to/enclosure.tif \
  --radiometry-lut path/to/enclosure.nc \
  --annotation-xml path/to/annotation.xml \
  --output-root ./output
```

Pass `--overwrite` to rebuild complete existing tile products.

### Local Item-ID runs

`stage-and-process-gamma0` is a development-only adapter: it searches MAAP,
downloads an Item's three source assets into `/tmp/esa-biomass-gamma0/`, writes a
sanitized source Item there, and delegates to the unchanged staged workflow.
A complete cache is reusable offline; use `--refresh` to stage the Item and
assets again.

First, query the MAAP STAC API for candidate IDs in an AOI. Replace the example
WGS84 bbox with your AOI:

```bash
uv run python - <<'PY' > /tmp/item-ids.txt
import logging
from pystac_client import Client

logger = logging.getLogger("esa-biomass-gamma0")

search = Client.open("https://catalog.maap.eo.esa.int/catalogue/").search(
    collections=["BiomassLevel1b"],
    bbox=[25, 62, 26, 63],
    max_items=10,
    sortby="-datetime",
)

items = list(search.items())
logger.info(f"found {len(items)} items")

for item in search.items():
    print(item.id)
PY
```

Set local MAAP credentials, then process the discovered IDs sequentially into
the shared `./output/` Catalog and Collection:

```bash
export ESA_MAAP_CLIENT_SECRET=...
export ESA_OFFLINE_TOKEN=...

while IFS= read -r item_id; do
  uv run stage-and-process-gamma0 "$item_id"
done < /tmp/item-ids.txt
```

For one Item, run `uv run stage-and-process-gamma0 <item-id>`. Pass
`--output-root <directory>` to use a different output root, `--overwrite` to
rebuild existing products, or `--refresh` to refresh its staged source files.

### Browse a local output root with eoAPI

Each run rebuilds the root `catalog.json` and `collection.json` from every valid
nested Item in its output root; the Collection spatial extent is their single
enclosing WGS84 bounding box. The local Compose stack runs PgSTAC, STAC API,
TiTiler, TiPG, and STAC Browser on the usual eoAPI ports. It mounts `./output`
read-only into TiTiler at
`/data/gamma0`. TiPG exposes the loaded PgSTAC Items as the
`pgstac.items` feature collection.

```bash
docker compose up --build -d

export PGHOST=127.0.0.1
export PGPORT=5439
export PGDATABASE=postgis
export PGUSER=username
export PGPASSWORD=password

uv run --with 'pypgstac[psycopg]==0.9.11' python scripts/load_pgstac.py ./output \
  --asset-root /data/gamma0
```

Open the STAC API at <http://localhost:8081>, TiTiler at
<http://localhost:8082>, TiPG at <http://localhost:8083/collections/pgstac.items>,
and STAC Browser at <http://localhost:8085>. The loader upserts the Collection
and its registered Items, so rerun it after adding local products.

Set `GAMMA0_OUTPUT_ROOT=/absolute/path/to/output` before `docker compose up` to
mount a different output root. The loader maps its relative COG and thumbnail
HREFs to `file:///data/gamma0/...` only in the database copy; generated STAC
files remain portable. `pypgstac` is fetched only for this command and is not
part of the DPS runtime; its version matches the bundled PgSTAC database.

## Parameters

- `source_item`: Source STAC Item JSON. It must reference `enclosure_tiff`,
  `enclosure_nc`, and `enclosure_annot_xml` assets.
- `beta0_tiff`: Four-band Beta0 `enclosure_tiff` asset.
- `radiometry_lut`: Radiometry `enclosure_nc` NetCDF asset.
- `annotation_xml`: `enclosure_annot_xml` asset.
- `resolution`: Output resolution in metres. Only `25` is accepted.
- `overwrite`: Rebuild an existing complete source-granule and MGRS-tile product.

## Output

Each accepted source granule and MGRS tile produces:

```text
<tile>/<acquisition-date>/<source-item-id>/
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

The job also writes `catalog.json` and `collection.json` at the output root.
Scientific COGs are single-band `float32` rasters with `-9999.0` nodata;
Gamma0 is linear intensity, not dB. The Collection's `item_assets` metadata
provides descriptive polarization-specific titles, media types, and roles for
all ten assets. It also provides `beta0-rgb` and `gamma0-rgb` Render-extension
presets for HH/HV/VV composites, using fixed 2nd-to-98th percentile stretches
sampled from the local validation products.

For implementation details and validation requirements, see
[`dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md`](dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md).

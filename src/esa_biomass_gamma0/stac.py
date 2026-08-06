"""STAC assembly, product validation, and local Catalog recovery."""

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import Transformer
from pystac import (
    Catalog,
    CatalogType,
    Collection,
    Extent,
    Item,
    ItemAssetDefinition,
    Link,
    Provider,
    ProviderRole,
    RelType,
    SpatialExtent,
    TemporalExtent,
)
from pystac.extensions.mgrs import MgrsExtension
from pystac.extensions.projection import ProjectionExtension
from pystac.extensions.raster import RasterBand, RasterExtension
from pystac.extensions.render import Render, RenderExtension
from pystac.extensions.sar import FrequencyBand, Polarization, SarExtension
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.transform import Affine, GCPTransformer, array_bounds

from esa_biomass_gamma0.grids import TileGrid
from esa_biomass_gamma0.raster import (
    NODATA,
    POLARIZATIONS,
    product_asset_filename,
    validate_scientific_cog,
    validate_thumbnail,
)
from esa_biomass_gamma0.source import StagedSource

COLLECTION_ID = "biomass-gamma0-mgrs-25m"
PACKAGE_NAME = "esa-biomass-gamma0"
REPOSITORY_URL = "https://github.com/MAAP-Project/esa-biomass-gamma0"
PROCESSING_EXTENSION_SCHEMA = (
    "https://stac-extensions.github.io/processing/v1.2.0/schema.json"
)
SCIENTIFIC_MEDIA_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
THUMBNAIL_KEY = "thumbnail"
ITEM_ASSETS = {
    **{
        f"beta0_{polarization.lower()}": ItemAssetDefinition(
            {
                "title": f"Beta0 {polarization} amplitude",
                "type": SCIENTIFIC_MEDIA_TYPE,
                "roles": ["data"],
            }
        )
        for polarization in POLARIZATIONS
    },
    **{
        f"gamma0_{polarization.lower()}": ItemAssetDefinition(
            {
                "title": f"Linear Gamma0 {polarization} intensity",
                "type": SCIENTIFIC_MEDIA_TYPE,
                "roles": ["data"],
            }
        )
        for polarization in POLARIZATIONS
    },
    "gamma0_lut": ItemAssetDefinition(
        {
            "title": "Bilinearly resampled Gamma0 multiplicative factor",
            "type": SCIENTIFIC_MEDIA_TYPE,
            "roles": ["data"],
        }
    ),
    THUMBNAIL_KEY: ItemAssetDefinition(
        {
            "title": "Gamma0 RGB thumbnail",
            "type": "image/png",
            "roles": ["thumbnail", "overview"],
        }
    ),
}
SCIENTIFIC_QUANTITIES = {
    "beta0": "beta0_amplitude",
    "gamma0": "gamma0_linear_intensity",
    "gamma0_lut": "gamma_nought_calibration_factor",
}


def source_footprint(
    grid: TileGrid,
    gcps: list[GroundControlPoint],
    gcp_crs: CRS | None,
    source_height: int,
    source_width: int,
) -> dict[str, Any] | None:
    """Return the GCP-derived source footprint clipped to a tile, if reliable."""
    if not gcps or gcp_crs is None or source_height < 2 or source_width < 2:
        return None
    rows, columns = _source_boundary(source_height, source_width)

    try:
        with GCPTransformer(gcps) as transformer:
            x, y = transformer.xy(rows, columns)

        to_grid = Transformer.from_crs(gcp_crs, grid.crs, always_xy=True)
        x, y = to_grid.transform(x, y)
        polygon = _clip_to_bounds(list(zip(x, y, strict=True)), grid.bounds)

        if len(polygon) < 3 or not np.isfinite(polygon).all():
            return None

        to_wgs84 = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
        longitude, latitude = to_wgs84.transform(*zip(*polygon, strict=True))

    except Exception:  # GDAL transformers can fail on malformed but present GCPs.
        return None

    coordinates = list(zip(longitude, latitude, strict=True))
    if not np.isfinite(coordinates).all():
        return None

    coordinates.append(coordinates[0])
    return {"type": "Polygon", "coordinates": [[list(point) for point in coordinates]]}


def build_item(
    source: StagedSource,
    grid: TileGrid,
    directory: Path,
    *,
    processing_version: str,
    geometry: dict[str, Any] | None = None,
) -> Item:
    """Build a validated Gamma0 Item for the completed assets in one leaf directory."""
    partial_coverage = geometry is None
    geometry = geometry or tile_geometry(grid)
    properties: dict[str, Any] = {
        "platform": "BIOMASS",
        "instruments": ["P-SAR"],
        "processing:software": {PACKAGE_NAME: processing_version},
    }

    if partial_coverage:
        properties["maap:partial_coverage"] = True

    item = Item(
        id=f"gamma0-{source.item_id}-{grid.tile_id}",
        geometry=geometry,
        bbox=_geometry_bbox(geometry),
        datetime=source.datetime,
        properties=properties,
    )
    item.stac_extensions.append(PROCESSING_EXTENSION_SCHEMA)
    item.add_link(
        Link(
            rel="processing-software",
            target=REPOSITORY_URL,
            media_type="text/html",
            title="ESA BIOMASS Gamma0 source repository",
        )
    )

    ProjectionExtension.ext(item, add_if_missing=True).apply(
        epsg=grid.epsg,
        shape=list(grid.shape),
        transform=list(grid.transform)[:6],
    )
    MgrsExtension.ext(item, add_if_missing=True).apply(*_mgrs_components(grid))
    SarExtension.ext(item, add_if_missing=True).apply(
        instrument_mode="P-SAR",
        frequency_band=FrequencyBand.P,
        polarizations=[Polarization(value) for value in POLARIZATIONS],
        product_type="Gamma0",
    )

    for key, definition in ITEM_ASSETS.items():
        filename = product_asset_filename(key, source.item_id, grid.tile_id)
        path = directory / filename
        if not path.is_file():
            raise ValueError(f"missing product asset: {path}")

        asset = definition.create_asset(path.name)
        item.add_asset(key, asset)

        if key == THUMBNAIL_KEY:
            continue

        ProjectionExtension.ext(asset).apply(
            epsg=grid.epsg,
            shape=list(grid.shape),
            transform=list(grid.transform)[:6],
        )
        RasterExtension.ext(asset, add_if_missing=True).apply(
            [RasterBand.create(nodata=float(NODATA), data_type="float32", unit="1")]
        )

        _, polarization = _scientific_asset_details(key)
        if polarization is not None:
            SarExtension.ext(asset).polarizations = [Polarization(polarization)]

    if source.self_href:
        item.add_link(Link.derived_from(source.self_href, title=source.item_id))

    for key in ("enclosure_tiff", "enclosure_nc"):
        item.add_link(Link(rel=RelType.VIA, target=source.asset_hrefs[key]))

    item.validate()

    return item


def write_item(item: Item, path: Path) -> None:
    """Validate and atomically write one STAC Item JSON document."""
    item.set_self_href(str(path))
    item.validate()
    _write_json(path, item.to_dict())


def is_complete_product(directory: Path) -> bool:
    """Return whether a leaf directory has a valid Item and every required local asset."""
    try:
        _validated_product(directory)
    except (OSError, ValueError):
        return False
    return True


def create_collection(items: list[Item]) -> Collection:
    """Build the optional Collection representation for a set of product Items."""
    datetimes = [item.datetime for item in items]
    bboxes = [item.bbox for item in items]
    spatial_bbox = (
        [
            min(bbox[0] for bbox in bboxes),
            min(bbox[1] for bbox in bboxes),
            max(bbox[2] for bbox in bboxes),
            max(bbox[3] for bbox in bboxes),
        ]
        if bboxes
        else [-180.0, -90.0, 180.0, 90.0]
    )
    processing_versions = sorted({_processing_version(item) for item in items})
    collection = Collection(
        id=COLLECTION_ID,
        description="Fixed-grid 25 m ESA BIOMASS Beta0 and linear Gamma0 MGRS products.",
        extent=Extent(
            SpatialExtent([spatial_bbox]),
            TemporalExtent(
                [[min(datetimes), max(datetimes)]] if datetimes else [[None, None]]
            ),
        ),
        providers=[
            Provider(
                name="MAAP Project",
                roles=[ProviderRole.PROCESSOR],
                url=REPOSITORY_URL,
                extra_fields={
                    "processing:software": {
                        PACKAGE_NAME: ", ".join(processing_versions) or "unknown"
                    }
                },
            )
        ],
    )
    collection.stac_extensions.append(PROCESSING_EXTENSION_SCHEMA)
    collection.add_link(
        Link(
            rel="processing-software",
            target=REPOSITORY_URL,
            media_type="text/html",
            title="ESA BIOMASS Gamma0 source repository",
        )
    )
    collection.item_assets = ITEM_ASSETS
    RenderExtension.ext(collection, add_if_missing=True).apply(
        {
            "beta0-rgb": Render.create(
                title="Beta0 HH/HV/VV RGB",
                assets=["beta0_hh", "beta0_hv", "beta0_vv"],
                rescale=[[0.1, 1.0], [0.025, 0.42], [0.12, 0.8]],
                nodata=float(NODATA),
            ),
            "gamma0-rgb": Render.create(
                title="Linear Gamma0 HH/HV/VV RGB",
                assets=["gamma0_hh", "gamma0_hv", "gamma0_vv"],
                rescale=[[0.005, 0.5], [0.0003, 0.09], [0.007, 0.3]],
                nodata=float(NODATA),
            ),
            "gamma0-correction-factor": Render.create(
                title="Gamma0 correction factor",
                assets=["gamma0_lut"],
                rescale=[[0, 1]],
                colormap_name="thermal",
                nodata=float(NODATA),
            ),
        }
    )
    for item in items:
        collection.add_item(item)
    return collection


def rebuild_catalog(output_root: Path) -> int:
    """Rebuild a root Catalog with direct Item links from valid products."""
    output_root.mkdir(parents=True, exist_ok=True)
    products = _discover_products(output_root)

    catalog = Catalog(
        id="biomass-gamma0-mgrs-25m-catalog",
        description="Local catalog of fixed-grid ESA BIOMASS Gamma0 products.",
    )

    catalog_path = output_root / "catalog.json"
    catalog.set_self_href(str(catalog_path))
    catalog.catalog_type = CatalogType.SELF_CONTAINED

    for path, item in products:
        item.set_self_href(str(path))
        catalog.add_link(
            Link(rel=RelType.ITEM, target=path.relative_to(output_root).as_posix())
        )

    for _, item in products:
        item.validate()

    catalog.validate()
    _write_json(catalog_path, catalog.to_dict(include_self_link=False))

    (output_root / "collection.json").unlink(missing_ok=True)

    return len(products)


def tile_geometry(grid: TileGrid) -> dict[str, Any]:
    """Return the exact tile boundary as a WGS84 GeoJSON polygon."""
    xmin, ymin, xmax, ymax = grid.bounds
    transformer = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    longitude, latitude = transformer.transform(
        (xmin, xmax, xmax, xmin, xmin), (ymin, ymin, ymax, ymax, ymin)
    )
    return {
        "type": "Polygon",
        "coordinates": [
            [list(point) for point in zip(longitude, latitude, strict=True)]
        ],
    }


def _validated_product(directory: Path) -> Item:
    """Load and validate one completed product leaf or raise a contextual error."""
    item_path = directory / "item.json"
    if not item_path.is_file():
        raise ValueError(f"missing Item: {item_path}")

    try:
        item = Item.from_file(str(item_path))
        item.validate()
    except Exception as error:
        raise ValueError(f"invalid Item: {item_path}: {error}") from error

    if set(item.assets) != set(ITEM_ASSETS):
        raise ValueError(f"invalid Item asset contract: {item_path}")

    grid = _item_grid(item)
    source_item_id = _source_item_id(item)

    try:
        processing_version = _processing_version(item)
    except ValueError as error:
        raise ValueError(f"invalid Item provenance: {item_path}") from error

    if not source_item_id:
        raise ValueError(f"invalid Item provenance: {item_path}")

    for key in ITEM_ASSETS:
        if key == THUMBNAIL_KEY:
            continue

        quantity, polarization = _scientific_asset_details(key)
        path = _local_asset_path(directory, item.assets[key].href)

        validate_scientific_cog(
            path,
            grid,
            quantity=quantity,
            polarization=polarization,
            source_item_id=source_item_id,
            processing_version=processing_version,
        )

    validate_thumbnail(_local_asset_path(directory, item.assets[THUMBNAIL_KEY].href))

    return item


def _scientific_asset_details(key: str) -> tuple[str, str | None]:
    """Return the COG quantity and optional polarization encoded by an asset key."""
    if key == "gamma0_lut":
        return SCIENTIFIC_QUANTITIES[key], None
    product, polarization = key.split("_", maxsplit=1)
    return SCIENTIFIC_QUANTITIES[product], polarization.upper()


def _discover_products(output_root: Path) -> list[tuple[Path, Item]]:
    """Return valid leaf Items while excluding unfinished sibling temporary directories."""
    products: list[tuple[Path, Item]] = []
    for path in sorted(output_root.rglob("item.json")):
        if any(part.startswith(".") for part in path.relative_to(output_root).parts):
            continue
        try:
            products.append((path, _validated_product(path.parent)))
        except (OSError, ValueError):
            continue

    return products


def _source_item_id(item: Item) -> str:
    """Recover the source ID from the contractually structured product Item ID."""
    try:
        tile_id = _item_tile_id(item)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid Item provenance") from error

    prefix, suffix = "gamma0-", f"-{tile_id}"
    if not item.id.startswith(prefix) or not item.id.endswith(suffix):
        raise ValueError("invalid Item provenance")

    source_item_id = item.id.removeprefix(prefix).removesuffix(suffix)
    if not source_item_id:
        raise ValueError("invalid Item provenance")

    return source_item_id


def _processing_version(item: Item) -> str:
    """Return this package's recorded Processing-extension software version."""
    software = item.properties.get("processing:software")
    if not isinstance(software, dict):
        raise ValueError("invalid processing software")

    processing_version = software.get(PACKAGE_NAME)
    if not isinstance(processing_version, str) or not processing_version:
        raise ValueError("invalid processing software")

    return processing_version


def _item_grid(item: Item) -> TileGrid:
    """Reconstruct a target-grid record from an Item's Projection metadata."""
    try:
        projection = ProjectionExtension.ext(item)
        epsg = projection.epsg
        shape = tuple(int(value) for value in projection.shape or [])
        transform = Affine(*(projection.transform or []))
        if epsg is None or len(shape) != 2 or min(shape) <= 0:
            raise ValueError("invalid projection metadata")

        bounds = tuple(float(value) for value in array_bounds(*shape, transform))
        tile_id = _item_tile_id(item)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid Item projection metadata") from error

    return TileGrid(
        tile_id=tile_id,
        epsg=epsg,
        bounds=bounds,
        crs=CRS.from_epsg(epsg),
        transform=transform,
        shape=shape,
    )


def _mgrs_components(grid: TileGrid) -> tuple[str, str, int]:
    """Return standard MGRS extension fields from an authoritative target grid."""
    return grid.tile_id[-3], grid.tile_id[-2:], grid.epsg % 100


def _item_tile_id(item: Item) -> str:
    """Recover a standard 100 km MGRS tile ID from its extension metadata."""
    mgrs = MgrsExtension.ext(item)
    if mgrs.utm_zone is None or mgrs.latitude_band is None or mgrs.grid_square is None:
        raise ValueError("invalid MGRS metadata")

    return f"{mgrs.utm_zone:02d}{mgrs.latitude_band}{mgrs.grid_square}"


def _local_asset_path(directory: Path, href: str) -> Path:
    """Resolve a required asset href and reject references outside its product leaf."""
    path = (directory / href).resolve()
    if directory.resolve() not in path.parents:
        raise ValueError(f"asset is not local to product: {href}")

    return path


def _source_boundary(height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    """Return a small closed pixel perimeter for GCP source-footprint estimation."""
    samples = np.linspace(0, 1, 33)
    rows = np.concatenate(
        (
            np.zeros_like(samples),
            samples[1:] * (height - 1),
            np.full_like(samples[1:], height - 1),
            (1 - samples[1:]) * (height - 1),
        )
    )
    columns = np.concatenate(
        (
            samples * (width - 1),
            np.full_like(samples[1:], width - 1),
            (1 - samples[1:]) * (width - 1),
            np.zeros_like(samples[1:]),
        )
    )
    return rows, columns


def _clip_to_bounds(
    polygon: list[tuple[float, float]], bounds: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """Clip a polygon to an axis-aligned tile rectangle."""
    xmin, ymin, xmax, ymax = bounds
    for inside, intersect in (
        (lambda point: point[0] >= xmin, lambda a, b: _intersect_x(a, b, xmin)),
        (lambda point: point[0] <= xmax, lambda a, b: _intersect_x(a, b, xmax)),
        (lambda point: point[1] >= ymin, lambda a, b: _intersect_y(a, b, ymin)),
        (lambda point: point[1] <= ymax, lambda a, b: _intersect_y(a, b, ymax)),
    ):
        clipped: list[tuple[float, float]] = []
        for start, end in zip(polygon, polygon[1:] + polygon[:1], strict=True):
            start_inside, end_inside = inside(start), inside(end)
            if start_inside:
                clipped.append(start)
            if start_inside != end_inside:
                clipped.append(intersect(start, end))

        polygon = clipped
        if not polygon:
            break

    return polygon


def _intersect_x(
    start: tuple[float, float], end: tuple[float, float], x: float
) -> tuple[float, float]:
    """Return a segment's intersection with one vertical clipping boundary."""
    ratio = (x - start[0]) / (end[0] - start[0])
    return x, start[1] + ratio * (end[1] - start[1])


def _intersect_y(
    start: tuple[float, float], end: tuple[float, float], y: float
) -> tuple[float, float]:
    """Return a segment's intersection with one horizontal clipping boundary."""
    ratio = (y - start[1]) / (end[1] - start[1])
    return start[0] + ratio * (end[0] - start[0]), y


def _geometry_bbox(geometry: dict[str, Any]) -> list[float]:
    """Return a GeoJSON polygon's horizontal bounding box."""
    coordinates = geometry.get("coordinates")
    if geometry.get("type") != "Polygon" or not coordinates:
        raise ValueError("geometry must be a non-empty Polygon")

    points = [point for ring in coordinates for point in ring]
    if not points or any(len(point) < 2 for point in points):
        raise ValueError("geometry must contain coordinate pairs")

    longitude = [float(point[0]) for point in points]
    latitude = [float(point[1]) for point in points]
    if not all(math.isfinite(value) for value in [*longitude, *latitude]):
        raise ValueError("geometry coordinates must be finite")

    return [min(longitude), min(latitude), max(longitude), max(latitude)]


def _write_json(path: Path, document: dict[str, Any]) -> None:
    """Atomically write one UTF-8 JSON document on its destination filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(document, file, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise

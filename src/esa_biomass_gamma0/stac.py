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
    Asset,
    Catalog,
    Collection,
    Extent,
    Item,
    Link,
    RelType,
    SpatialExtent,
    TemporalExtent,
)
from pystac.extensions.projection import ProjectionExtension
from pystac.extensions.raster import RasterBand, RasterExtension
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.transform import Affine, GCPTransformer, array_bounds

from esa_biomass_gamma0.grids import TileGrid
from esa_biomass_gamma0.raster import (
    NODATA,
    POLARIZATIONS,
    validate_scientific_cog,
    validate_thumbnail,
)
from esa_biomass_gamma0.source import StagedSource

COLLECTION_ID = "biomass-gamma0-mgrs-25m"
SCIENTIFIC_ASSETS = {
    **{
        f"beta0_{polarization.lower()}": (
            "beta0_amplitude",
            polarization,
            ["data", "beta0"],
        )
        for polarization in POLARIZATIONS
    },
    **{
        f"gamma0_{polarization.lower()}": (
            "gamma0_linear_intensity",
            polarization,
            ["data", "gamma0"],
        )
        for polarization in POLARIZATIONS
    },
    "gamma_nought": ("gamma_nought_calibration_factor", None, ["data", "calibration"]),
}
THUMBNAIL_KEY = "thumbnail"


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
        "mgrs:tile": grid.tile_id,
        "platform": "BIOMASS",
        "instruments": ["P-SAR"],
        "sar:polarizations": list(POLARIZATIONS),
        "processing:level": "Gamma0",
        "maap:processing_version": processing_version,
    }
    if partial_coverage:
        properties["maap:partial_coverage"] = True
    item = Item(
        id=f"gamma0-{source.item_id}-{grid.tile_id}",
        geometry=geometry,
        bbox=_geometry_bbox(geometry),
        datetime=source.datetime,
        properties=properties,
        collection=COLLECTION_ID,
    )
    item.add_link(Link(rel="collection", target="../../../collection.json"))
    ProjectionExtension.ext(item, add_if_missing=True).apply(
        epsg=grid.epsg,
        shape=list(grid.shape),
        transform=list(grid.transform)[:6],
    )
    for key, (quantity, polarization, roles) in SCIENTIFIC_ASSETS.items():
        path = directory / f"{key}.tif"
        if not path.is_file():
            raise ValueError(f"missing scientific asset: {path}")
        asset = Asset(
            href=path.name,
            media_type="image/tiff; application=geotiff; profile=cloud-optimized",
            roles=roles,
            title=quantity,
        )
        item.add_asset(key, asset)
        ProjectionExtension.ext(asset).apply(
            epsg=grid.epsg,
            shape=list(grid.shape),
            transform=list(grid.transform)[:6],
        )
        RasterExtension.ext(asset, add_if_missing=True).apply(
            [RasterBand.create(nodata=float(NODATA), data_type="float32", unit="1")]
        )
        if polarization is not None:
            asset.extra_fields["sar:polarizations"] = [polarization]
    thumbnail = directory / "thumbnail.png"
    if not thumbnail.is_file():
        raise ValueError(f"missing thumbnail asset: {thumbnail}")
    item.add_asset(
        THUMBNAIL_KEY,
        Asset(
            href=thumbnail.name, media_type="image/png", roles=["thumbnail", "overview"]
        ),
    )
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


def rebuild_catalog(output_root: Path) -> int:
    """Rebuild root Catalog and Collection files from valid non-temporary products."""
    output_root.mkdir(parents=True, exist_ok=True)
    products = _discover_products(output_root)
    datetimes = [item.datetime for _, item in products]
    bboxes = [item.bbox for _, item in products]
    extent = Extent(
        SpatialExtent(bboxes or [[-180.0, -90.0, 180.0, 90.0]]),
        TemporalExtent(
            [[min(datetimes), max(datetimes)]] if datetimes else [[None, None]]
        ),
    )
    collection = Collection(
        id=COLLECTION_ID,
        description="Fixed-grid 25 m ESA BIOMASS Beta0 and linear Gamma0 MGRS products.",
        extent=extent,
    )
    catalog = Catalog(
        id="biomass-gamma0-mgrs-25m-catalog",
        description="Local catalog of fixed-grid ESA BIOMASS Gamma0 products.",
    )
    catalog_path = output_root / "catalog.json"
    collection_path = output_root / "collection.json"
    catalog.set_self_href(str(catalog_path))
    collection.set_self_href(str(collection_path))
    catalog.add_link(Link(rel="child", target="collection.json"))
    collection.add_link(Link(rel="root", target="catalog.json"))
    for path, item in products:
        collection.add_item(item)
        item.set_self_href(str(path))

    for _, item in products:
        item.validate()
    collection.validate()
    catalog.validate()
    _write_json(collection_path, collection.to_dict())
    _write_json(catalog_path, catalog.to_dict())
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
    if item.collection_id != COLLECTION_ID or set(item.assets) != {
        *SCIENTIFIC_ASSETS,
        THUMBNAIL_KEY,
    }:
        raise ValueError(f"invalid Item asset contract: {item_path}")
    grid = _item_grid(item)
    source_item_id = _source_item_id(item)
    processing_version = str(item.properties.get("maap:processing_version", ""))
    if not source_item_id or not processing_version:
        raise ValueError(f"invalid Item provenance: {item_path}")
    for key, (quantity, polarization, _) in SCIENTIFIC_ASSETS.items():
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
        tile_id = str(item.properties["mgrs:tile"])
    except KeyError as error:
        raise ValueError("invalid Item provenance") from error
    prefix, suffix = "gamma0-", f"-{tile_id}"
    if not item.id.startswith(prefix) or not item.id.endswith(suffix):
        raise ValueError("invalid Item provenance")
    source_item_id = item.id.removeprefix(prefix).removesuffix(suffix)
    if not source_item_id:
        raise ValueError("invalid Item provenance")
    return source_item_id


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
        tile_id = str(item.properties["mgrs:tile"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid Item projection metadata") from error
    return TileGrid(
        tile_id=tile_id,
        epsg=epsg,
        bounds=bounds,
        crs=CRS.from_epsg(epsg),
        transform=transform,
        shape=shape,
    )


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

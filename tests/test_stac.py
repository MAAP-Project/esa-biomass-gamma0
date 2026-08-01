"""Tests for Gamma0 product STAC assembly and local catalog recovery."""

from pathlib import Path

from affine import Affine
from pystac import Catalog, Collection, RelType
from pystac.extensions.projection import ProjectionExtension
from pystac.extensions.raster import RasterExtension
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS

from conftest import write_item as write_source_item
from esa_biomass_gamma0.grids import TileGrid
from esa_biomass_gamma0.raster import write_scientific_cogs, write_thumbnail
from esa_biomass_gamma0.source import validate_staged_source
from esa_biomass_gamma0.stac import (
    COLLECTION_ID,
    build_item,
    is_complete_product,
    rebuild_catalog,
    source_footprint,
    write_item,
)


def _grid() -> TileGrid:
    """Build a small tile grid around the staged-source fixture."""
    bounds = (600_000.0, 5_000_000.0, 600_250.0, 5_000_250.0)
    return TileGrid(
        tile_id="32TPR",
        epsg=32632,
        bounds=bounds,
        crs=CRS.from_epsg(32632),
        transform=Affine.translation(bounds[0], bounds[3]) * Affine.scale(25, -25),
        shape=(10, 10),
    )


def _arrays() -> tuple[object, object, object]:
    """Return small four-polarization arrays for real asset creation."""
    import numpy as np

    beta0 = np.stack(
        [np.arange(100, dtype="float32").reshape(10, 10) + index for index in range(4)]
    )
    gamma0 = beta0**2 * 0.5
    return beta0, gamma0, np.full((10, 10), 0.5, dtype="float32")


def _product(
    tmp_path: Path, staged_paths: dict[str, Path]
) -> tuple[Path, object, TileGrid]:
    """Write a complete small product ready for STAC assembly."""
    write_source_item(staged_paths["source_item"])
    source = validate_staged_source(**staged_paths)
    grid = _grid()
    directory = (
        tmp_path / grid.tile_id / source.datetime.date().isoformat() / source.item_id
    )
    directory.mkdir(parents=True)
    beta0, gamma0, gamma_nought = _arrays()
    write_scientific_cogs(
        directory,
        grid,
        beta0,
        gamma0,
        gamma_nought,
        source_item_id=source.item_id,
        processing_version="0.1.0",
    )
    write_thumbnail(directory / "thumbnail.png", gamma0)
    return directory, source, grid


def test_builds_a_valid_item_for_complete_local_assets(
    tmp_path: Path, staged_paths: dict[str, Path]
) -> None:
    """One product Item carries its tile grid, data assets, and safe provenance."""
    directory, source, grid = _product(tmp_path, staged_paths)
    gcps = [
        GroundControlPoint(row=0, col=0, x=grid.bounds[0], y=grid.bounds[3]),
        GroundControlPoint(row=0, col=9, x=grid.bounds[2], y=grid.bounds[3]),
        GroundControlPoint(row=9, col=9, x=grid.bounds[2], y=grid.bounds[1]),
        GroundControlPoint(row=9, col=0, x=grid.bounds[0], y=grid.bounds[1]),
    ]

    item = build_item(
        source,
        grid,
        directory,
        processing_version="0.1.0",
        geometry=source_footprint(grid, gcps, grid.crs, 10, 10),
    )
    write_item(item, directory / "item.json")

    assert item.id == "gamma0-BIOMASS_TEST_001-32TPR"
    assert item.collection_id == COLLECTION_ID
    assert set(item.assets) == {
        "beta0_hh",
        "beta0_hv",
        "beta0_vh",
        "beta0_vv",
        "gamma0_hh",
        "gamma0_hv",
        "gamma0_vh",
        "gamma0_vv",
        "gamma_nought",
        "thumbnail",
    }
    assert item.properties["mgrs:tile"] == grid.tile_id
    assert item.properties["sar:polarizations"] == ["HH", "HV", "VH", "VV"]
    assert "maap:source_item_id" not in item.properties
    assert "maap:source_collection" not in item.properties
    source_links = item.get_links(RelType.DERIVED_FROM)
    assert len(source_links) == 1
    assert source_links[0].target == "https://example.test/items/test.json"
    assert source_links[0].title == source.item_id
    assert [link.target for link in item.get_links(RelType.VIA)] == [
        "https://example.test/beta.tif",
        "https://example.test/lut.nc",
    ]
    assert "maap:partial_coverage" not in item.properties
    assert ProjectionExtension.ext(item).epsg == grid.epsg
    assert RasterExtension.ext(item.assets["gamma0_hh"]).bands[0].nodata == -9999.0
    assert item.assets["gamma0_hh"].roles == ["data", "gamma0"]
    assert item.assets["thumbnail"].roles == ["thumbnail", "overview"]
    assert "secret" not in str(item.to_dict())
    item.validate()
    assert is_complete_product(directory)
    assert (
        build_item(source, grid, directory, processing_version="0.1.0").properties[
            "maap:partial_coverage"
        ]
        is True
    )


def test_rebuild_catalog_recovers_valid_products_and_handles_empty_results(
    tmp_path: Path, staged_paths: dict[str, Path]
) -> None:
    """Catalog rebuilding registers valid leaves and preserves the empty contract."""
    assert rebuild_catalog(tmp_path) == 0
    empty = Collection.from_file(tmp_path / "collection.json")
    assert empty.extent.spatial.bboxes == [[-180.0, -90.0, 180.0, 90.0]]
    assert empty.extent.temporal.intervals == [[None, None]]

    directory, source, grid = _product(tmp_path, staged_paths)
    item = build_item(source, grid, directory, processing_version="0.1.0")
    write_item(item, directory / "item.json")
    (tmp_path / "catalog.json").unlink()

    assert rebuild_catalog(tmp_path) == 1
    catalog = Catalog.from_file(tmp_path / "catalog.json")
    collection = next(catalog.get_children())
    assert collection.id == COLLECTION_ID
    assert [product.id for product in collection.get_items()] == [item.id]
    assert collection.extent.temporal.intervals == [[source.datetime, source.datetime]]

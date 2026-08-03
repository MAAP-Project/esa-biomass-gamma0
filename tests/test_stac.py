"""Tests for Gamma0 product STAC assembly and local catalog recovery."""

from pathlib import Path

from affine import Affine
from conftest import write_item as write_source_item
from pystac import Catalog, Collection, RelType
from pystac.extensions.projection import ProjectionExtension
from pystac.extensions.raster import RasterExtension
from pystac.extensions.render import RenderExtension
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS

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


def _grid(
    tile_id: str = "32TPR",
    bounds: tuple[float, float, float, float] = (
        600_000.0,
        5_000_000.0,
        600_250.0,
        5_000_250.0,
    ),
) -> TileGrid:
    """Build a small tile grid around the staged-source fixture."""
    return TileGrid(
        tile_id=tile_id,
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
    tmp_path: Path, staged_paths: dict[str, Path], grid: TileGrid | None = None
) -> tuple[Path, object, TileGrid]:
    """Write a complete small product ready for STAC assembly."""
    write_source_item(staged_paths["source_item"])
    source = validate_staged_source(**staged_paths)
    grid = grid or _grid()
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
    assert item.assets["beta0_hh"].title == "Beta0 HH amplitude"
    assert item.assets["gamma0_hh"].title == "Linear Gamma0 HH intensity"
    assert item.assets["gamma0_hh"].roles == ["data", "gamma0"]
    assert item.assets["thumbnail"].title == "Gamma0 RGB thumbnail"
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
    assert set(empty.item_assets) == {
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
    assert set(RenderExtension.ext(empty).renders) == {
        "beta0-rgb",
        "gamma0-rgb",
        "gamma0-correction-factor",
    }

    directory, source, grid = _product(tmp_path, staged_paths)
    item = build_item(source, grid, directory, processing_version="0.1.0")
    write_item(item, directory / "item.json")
    other_directory, _, other_grid = _product(
        tmp_path,
        staged_paths,
        _grid("32TQS", (700_000.0, 5_100_000.0, 700_250.0, 5_100_250.0)),
    )
    other_item = build_item(
        source, other_grid, other_directory, processing_version="0.1.0"
    )
    write_item(other_item, other_directory / "item.json")
    (tmp_path / "catalog.json").unlink()

    assert rebuild_catalog(tmp_path) == 2
    catalog = Catalog.from_file(tmp_path / "catalog.json")
    collection = next(catalog.get_children())
    assert collection.id == COLLECTION_ID
    assert [product.id for product in collection.get_items()] == [
        item.id,
        other_item.id,
    ]
    assert collection.extent.temporal.intervals == [[source.datetime, source.datetime]]
    assert item.bbox is not None
    assert other_item.bbox is not None
    assert collection.extent.spatial.bboxes == [
        [
            min(item.bbox[0], other_item.bbox[0]),
            min(item.bbox[1], other_item.bbox[1]),
            max(item.bbox[2], other_item.bbox[2]),
            max(item.bbox[3], other_item.bbox[3]),
        ]
    ]
    assert {
        key: render.to_dict()
        for key, render in RenderExtension.ext(collection).renders.items()
    } == {
        "beta0-rgb": {
            "title": "Beta0 HH/HV/VV RGB",
            "assets": ["beta0_hh", "beta0_hv", "beta0_vv"],
            "rescale": [[0.1, 1.0], [0.025, 0.42], [0.12, 0.8]],
            "nodata": -9999.0,
        },
        "gamma0-rgb": {
            "title": "Linear Gamma0 HH/HV/VV RGB",
            "assets": ["gamma0_hh", "gamma0_hv", "gamma0_vv"],
            "rescale": [[0.005, 0.5], [0.0003, 0.09], [0.007, 0.3]],
            "nodata": -9999.0,
        },
        "gamma0-correction-factor": {
            "title": "Gamma0 correction factor",
            "assets": ["gamma_nought"],
            "rescale": [[0, 1]],
            "colormap_name": "thermal",
            "nodata": -9999.0,
        },
    }
    assert {
        key: definition.to_dict() for key, definition in collection.item_assets.items()
    } == {
        key: {"title": asset.title, "type": asset.media_type, "roles": asset.roles}
        for key, asset in item.assets.items()
    }

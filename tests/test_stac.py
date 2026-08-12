"""Tests for Gamma0 product STAC assembly and local catalog recovery."""

import json
from pathlib import Path

from affine import Affine
from conftest import write_item as write_source_item
from pystac import Catalog, RelType
from pystac.extensions.mgrs import MgrsExtension
from pystac.extensions.projection import ProjectionExtension
from pystac.extensions.raster import RasterExtension
from pystac.extensions.render import RenderExtension
from pystac.extensions.sar import FrequencyBand, Polarization, SarExtension
import numpy as np
from pyproj import Transformer
from rasterio.crs import CRS

from esa_biomass_gamma0 import __version__ as PACKAGE_VERSION
from esa_biomass_gamma0.grids import TileGrid
from esa_biomass_gamma0.raster import (
    product_asset_filename,
    write_scientific_cogs,
    write_thumbnail,
)
from esa_biomass_gamma0.source import validate_staged_source
from esa_biomass_gamma0.stac import (
    COLLECTION_ID,
    build_item,
    create_collection,
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
        processing_version=PACKAGE_VERSION,
    )
    write_thumbnail(
        directory / product_asset_filename("thumbnail", source.item_id, grid.tile_id),
        gamma0,
    )
    return directory, source, grid


def test_builds_a_valid_item_for_complete_local_assets(
    tmp_path: Path, staged_paths: dict[str, Path]
) -> None:
    """One product Item carries its tile grid, data assets, and safe provenance."""
    directory, source, grid = _product(tmp_path, staged_paths)
    x, y = np.meshgrid(
        np.linspace(grid.bounds[0], grid.bounds[2], 10),
        np.linspace(grid.bounds[3], grid.bounds[1], 10),
    )
    geolocation = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(x, y)

    item = build_item(
        source,
        grid,
        directory,
        processing_version=PACKAGE_VERSION,
        geometry=source_footprint(grid, geolocation),
    )
    write_item(item, directory / "item.json")

    assert item.id == "gamma0-BIOMASS_TEST_001-32TPR"
    assert item.collection_id is None
    assert item.get_links("collection") == []
    assert set(item.assets) == {
        "beta0_hh",
        "beta0_hv",
        "beta0_vh",
        "beta0_vv",
        "gamma0_hh",
        "gamma0_hv",
        "gamma0_vh",
        "gamma0_vv",
        "gamma0_lut",
        "thumbnail",
    }
    mgrs = MgrsExtension.ext(item)
    assert (mgrs.utm_zone, mgrs.latitude_band, mgrs.grid_square) == (32, "T", "PR")
    sar = SarExtension.ext(item)
    assert sar.instrument_mode == "P-SAR"
    assert sar.frequency_band == FrequencyBand.P
    assert sar.polarizations == [
        Polarization.HH,
        Polarization.HV,
        Polarization.VH,
        Polarization.VV,
    ]
    assert sar.product_type == "Gamma0"
    assert item.properties["processing:software"] == {
        "esa-biomass-gamma0": PACKAGE_VERSION
    }
    assert "processing:level" not in item.properties
    assert item.get_links("processing-software")[0].target == (
        "https://github.com/MAAP-Project/esa-biomass-gamma0"
    )
    assert SarExtension.ext(item.assets["gamma0_hh"]).polarizations == [Polarization.HH]
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
    assert item.assets["beta0_hh"].href == product_asset_filename(
        "beta0_hh", source.item_id, grid.tile_id
    )
    assert item.assets["beta0_hh"].title == "Beta0 HH amplitude"
    assert item.assets["gamma0_hh"].title == "Linear Gamma0 HH intensity"
    assert item.assets["gamma0_hh"].roles == ["data"]
    assert item.assets["thumbnail"].title == "Gamma0 RGB thumbnail"
    assert item.assets["thumbnail"].roles == ["thumbnail", "overview"]
    assert "secret" not in str(item.to_dict())
    item.validate()
    assert is_complete_product(directory)
    assert (
        build_item(
            source, grid, directory, processing_version=PACKAGE_VERSION
        ).properties["maap:partial_coverage"]
        is True
    )


def test_rebuild_catalog_links_products_directly_and_keeps_collection_optional(
    tmp_path: Path, staged_paths: dict[str, Path]
) -> None:
    """Catalog rebuilding links valid leaves directly without writing a Collection."""
    assert rebuild_catalog(tmp_path) == 0
    assert list(Catalog.from_file(tmp_path / "catalog.json").get_items()) == []
    assert not (tmp_path / "collection.json").exists()

    directory, source, grid = _product(tmp_path, staged_paths)
    item = build_item(source, grid, directory, processing_version=PACKAGE_VERSION)
    write_item(item, directory / "item.json")
    other_directory, _, other_grid = _product(
        tmp_path,
        staged_paths,
        _grid("32TQS", (700_000.0, 5_100_000.0, 700_250.0, 5_100_250.0)),
    )
    other_item = build_item(
        source, other_grid, other_directory, processing_version=PACKAGE_VERSION
    )
    write_item(other_item, other_directory / "item.json")
    assert set(item.assets[key].href for key in item.assets).isdisjoint(
        other_item.assets[key].href for key in other_item.assets
    )
    (tmp_path / "collection.json").write_text("obsolete", encoding="utf-8")

    assert rebuild_catalog(tmp_path) == 2
    catalog_path = tmp_path / "catalog.json"
    catalog = Catalog.from_file(catalog_path)
    document = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert [link for link in document["links"] if link["rel"] == "self"] == []
    assert (
        next(link for link in document["links"] if link["rel"] == "root")["href"]
        == "./catalog.json"
    )
    assert [product.id for product in catalog.get_items()] == [item.id, other_item.id]
    assert list(catalog.get_children()) == []
    assert not (tmp_path / "collection.json").exists()

    collection = create_collection([item, other_item])
    assert collection.id == COLLECTION_ID
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
    assert set(collection.item_assets) == set(item.assets)
    assert set(RenderExtension.ext(collection).renders) == {
        "beta0-rgb",
        "gamma0-rgb",
        "gamma0-correction-factor",
    }
    assert collection.providers[0].extra_fields["processing:software"] == {
        "esa-biomass-gamma0": PACKAGE_VERSION
    }
    assert collection.get_links("processing-software")[0].target == (
        "https://github.com/MAAP-Project/esa-biomass-gamma0"
    )
    collection.validate()

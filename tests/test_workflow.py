"""Tests for the staged-source Gamma0 product workflow."""

from pathlib import Path

from affine import Affine
from netCDF4 import Dataset
import numpy as np
import pytest
from rasterio import open as open_raster
from pyproj import Transformer
from rasterio.crs import CRS

from conftest import write_item as write_source_item
from esa_biomass_gamma0.grids import TileGrid
from esa_biomass_gamma0.stac import is_complete_product
from esa_biomass_gamma0.workflow import process_source


def _grid() -> TileGrid:
    """Build a small deterministic grid instead of writing 4000-pixel test COGs."""
    bounds = (600_000.0, 5_000_000.0, 600_250.0, 5_000_250.0)
    return TileGrid(
        tile_id="32TPR",
        epsg=32632,
        bounds=bounds,
        crs=CRS.from_epsg(32632),
        transform=Affine.translation(bounds[0], bounds[3]) * Affine.scale(25, -25),
        shape=(10, 10),
    )


def _write_beta0(path: Path, *, nodata: bool = False) -> None:
    """Write a four-band local staged Beta0 raster without embedded GCPs."""
    data = np.full((4, 10, 10), -9999.0 if nodata else 2.0, dtype="float32")
    with open_raster(
        path,
        "w",
        driver="GTiff",
        height=10,
        width=10,
        count=4,
        dtype="float32",
        nodata=-9999.0,
    ) as dataset:
        dataset.write(data)


def _write_lut(path: Path, grid: TileGrid) -> None:
    """Write a ten-by-ten GammaNought LUT in the annotation coordinate system."""
    with Dataset(path, "w") as dataset:
        dataset.createDimension("azimuth", 10)
        dataset.createDimension("range", 10)
        azimuth = dataset.createVariable("relativeAzimuthTimeRGC", "f8", ("azimuth",))
        slant_range = dataset.createVariable("slantRangeTimeRGC", "f8", ("range",))
        azimuth[:] = np.arange(10)
        slant_range[:] = np.arange(10)
        radiometry = dataset.createGroup("radiometry")
        gamma_nought = radiometry.createVariable(
            "gammaNought", "f4", ("azimuth", "range")
        )
        gamma_nought[:] = 0.5
        geometry = dataset.createGroup("geometry")
        longitude = geometry.createVariable("longitude", "f8", ("azimuth", "range"))
        latitude = geometry.createVariable("latitude", "f8", ("azimuth", "range"))
        x, y = np.meshgrid(
            np.linspace(grid.bounds[0], grid.bounds[2], 10),
            np.linspace(grid.bounds[3], grid.bounds[1], 10),
        )
        longitude[:], latitude[:] = Transformer.from_crs(
            grid.crs, "EPSG:4326", always_xy=True
        ).transform(x, y)


def _write_annotation(path: Path) -> None:
    """Write the minimum physical-coordinate calibration metadata."""
    path.write_text(
        """<root><sarImage><azimuthTimeInterval>1</azimuthTimeInterval>
        <rangePixelSpacing>1</rangePixelSpacing>
        <rangeCoordinateConversion><coordinateConversion>
        <groundToSlantCoefficients>0 1</groundToSlantCoefficients>
        </coordinateConversion></rangeCoordinateConversion></sarImage></root>""",
        encoding="utf-8",
    )


def _staged_source(
    staged_paths: dict[str, Path], grid: TileGrid, **beta0: object
) -> None:
    """Replace generic staged fixtures with real workflow inputs."""
    write_source_item(staged_paths["source_item"])
    _write_beta0(staged_paths["beta0_tiff"], **beta0)
    _write_lut(staged_paths["radiometry_lut"], grid)
    _write_annotation(staged_paths["annotation_xml"])


def test_processes_and_recovers_one_complete_tile_product(
    tmp_path: Path, staged_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete run replaces assets on every run and rebuilds STAC."""
    from esa_biomass_gamma0 import workflow

    grid = _grid()
    _staged_source(staged_paths, grid)
    monkeypatch.setattr(workflow, "candidate_grids", lambda _: [grid])
    output_root = tmp_path / "output"

    result = process_source(**staged_paths, output_root=output_root)
    directory = output_root / grid.tile_id / "2026-07-31" / "BIOMASS_TEST_001"

    assert result.candidates == 1
    assert result.written == 1
    assert result.failed == 0
    assert is_complete_product(directory)
    assert (output_root / "catalog.json").is_file()
    assert not (output_root / "collection.json").exists()

    repeated = process_source(**staged_paths, output_root=output_root)
    assert repeated.written == 1
    assert repeated.failed == 0
    assert is_complete_product(directory)


def test_skips_all_nodata_tiles_without_requiring_embedded_gcps(
    tmp_path: Path, staged_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expected tile rejection writes only the valid empty root STAC documents."""
    from esa_biomass_gamma0 import workflow

    grid = _grid()
    _staged_source(staged_paths, grid, nodata=True)
    monkeypatch.setattr(workflow, "candidate_grids", lambda _: [grid])

    result = process_source(**staged_paths, output_root=tmp_path / "output")
    assert result.written == 0
    assert result.skipped_no_data == 1
    assert result.failed == 0
    assert not list((tmp_path / "output").rglob("item.json"))

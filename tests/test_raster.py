"""Tests for direct fixed-grid warps and tile raster assets."""

from pathlib import Path
import warnings

from affine import Affine
import numpy as np
import pytest
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS

from esa_biomass_gamma0.grids import TileGrid
from esa_biomass_gamma0.raster import (
    POLARIZATIONS,
    THUMBNAIL_POLARIZATIONS,
    warp_scientific_arrays,
    write_scientific_cogs,
    write_thumbnail,
)


def _grid() -> TileGrid:
    """Build a small UTM grid for deterministic raster tests."""
    bounds = (600_000.0, 5_000_000.0, 600_250.0, 5_000_250.0)
    return TileGrid(
        tile_id="32TPR",
        epsg=32632,
        bounds=bounds,
        crs=CRS.from_epsg(32632),
        transform=Affine.translation(bounds[0], bounds[3]) * Affine.scale(25, -25),
        shape=(10, 10),
    )


def _gcps(grid: TileGrid) -> list[GroundControlPoint]:
    """Map a ten-pixel source directly onto the synthetic target grid."""
    xmin, ymin, xmax, ymax = grid.bounds
    return [
        GroundControlPoint(row=0, col=0, x=xmin, y=ymax),
        GroundControlPoint(row=0, col=9, x=xmax, y=ymax),
        GroundControlPoint(row=9, col=0, x=xmin, y=ymin),
        GroundControlPoint(row=9, col=9, x=xmax, y=ymin),
    ]


def _arrays() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Create matching four-polarization scientific source arrays."""
    beta0 = np.stack(
        [np.arange(100, dtype="float32").reshape(10, 10) + index for index in range(4)]
    )
    beta0[:, :2, :2] = np.nan
    gamma0 = beta0**2 * 0.5
    gamma_nought = np.full((10, 10), 0.5, dtype="float32")
    gamma_nought[:2, :2] = np.nan
    return beta0, gamma0, gamma_nought


def test_warps_beta0_gamma0_and_lut_directly_to_the_target_grid() -> None:
    """Each scientific source array gets one direct GCP-to-UTM bilinear warp."""
    grid = _grid()
    beta0, gamma0, gamma_nought = _arrays()

    warped = warp_scientific_arrays(
        beta0, gamma0, gamma_nought, _gcps(grid), grid.crs, grid
    )

    assert warped is not None
    warped_beta0, warped_gamma0, warped_lut = warped
    assert (
        warped_beta0.shape == warped_gamma0.shape == (len(POLARIZATIONS), *grid.shape)
    )
    assert warped_lut.shape == grid.shape
    assert np.isfinite(warped_beta0).any()
    assert np.isfinite(warped_gamma0).any()
    assert np.isfinite(warped_lut).any()


@pytest.mark.parametrize(
    ("beta0", "gamma0", "gamma_nought", "gcps", "gcp_crs", "message"),
    [
        (
            np.ones((3, 10, 10), dtype="float32"),
            np.ones((3, 10, 10), dtype="float32"),
            np.ones((10, 10), dtype="float32"),
            "valid",
            "valid",
            "four polarizations",
        ),
        (
            np.ones((4, 10, 10), dtype="float32"),
            np.ones((4, 9, 10), dtype="float32"),
            np.ones((10, 10), dtype="float32"),
            "valid",
            "valid",
            "same shape",
        ),
        (
            np.ones((4, 10, 10), dtype="float32"),
            np.ones((4, 10, 10), dtype="float32"),
            np.ones((9, 10), dtype="float32"),
            "valid",
            "valid",
            "window shape",
        ),
        (
            np.ones((4, 10, 10), dtype="float32"),
            np.ones((4, 10, 10), dtype="float32"),
            np.ones((10, 10), dtype="float32"),
            "valid",
            None,
            "GCP CRS",
        ),
    ],
)
def test_rejects_invalid_scientific_warp_inputs(
    beta0: np.ndarray,
    gamma0: np.ndarray,
    gamma_nought: np.ndarray,
    gcps: str,
    gcp_crs: str | None,
    message: str,
) -> None:
    """Warp inputs must have complete local georeferencing and matching arrays."""
    grid = _grid()

    with pytest.raises(ValueError, match=message):
        warp_scientific_arrays(
            beta0,
            gamma0,
            gamma_nought,
            _gcps(grid) if gcps == "valid" else [],
            grid.crs if gcp_crs == "valid" else None,
            grid,
        )


def test_returns_none_when_every_direct_warp_is_nodata() -> None:
    """An all-nodata target is a rejected tile rather than a product."""
    grid = _grid()
    empty = np.full((4, 10, 10), np.nan, dtype="float32")

    assert (
        warp_scientific_arrays(
            empty,
            empty,
            np.ones(grid.shape, dtype="float32"),
            _gcps(grid),
            grid.crs,
            grid,
        )
        is None
    )


def test_rejects_invalid_staged_arrays_before_writing_assets(tmp_path: Path) -> None:
    """A failed staged product write leaves no incomplete scientific asset behind."""
    directory = tmp_path / ".gamma0-product-tmp"
    directory.mkdir()
    beta0, gamma0, gamma_nought = _arrays()

    with pytest.raises(ValueError, match="same shape"):
        write_scientific_cogs(
            directory,
            _grid(),
            beta0,
            gamma0[:, :-1],
            gamma_nought,
            source_item_id="BIOMASS_TEST_001",
            processing_version="0.1.0",
        )

    assert not list(directory.iterdir())


def test_writes_and_validates_nine_shared_grid_cogs_and_rgb_thumbnail(
    tmp_path: Path,
) -> None:
    """Scientific assets have the required COG contract and display mapping."""
    grid = _grid()
    beta0, gamma0, gamma_nought = _arrays()
    directory = tmp_path / ".gamma0-product-tmp"
    directory.mkdir()

    paths = write_scientific_cogs(
        directory,
        grid,
        beta0,
        gamma0,
        gamma_nought,
        source_item_id="BIOMASS_TEST_001",
        processing_version="0.1.0",
    )
    import rasterio

    with pytest.warns(rasterio.errors.NotGeoreferencedWarning):
        thumbnail = write_thumbnail(directory / "thumbnail.png", gamma0)

    assert set(paths) == {
        *(f"beta0_{polarization.lower()}" for polarization in POLARIZATIONS),
        *(f"gamma0_{polarization.lower()}" for polarization in POLARIZATIONS),
        "gamma_nought",
    }
    assert thumbnail.exists()
    assert THUMBNAIL_POLARIZATIONS == ("HH", "HV", "VV")

    datasets = [rasterio.open(path) for path in paths.values()]
    try:
        assert all(
            dataset.count == 1 and dataset.dtypes == ("float32",)
            for dataset in datasets
        )
        assert all(
            dataset.crs == grid.crs and dataset.transform == grid.transform
            for dataset in datasets
        )
        assert all(
            (dataset.height, dataset.width) == grid.shape for dataset in datasets
        )
        assert all(dataset.nodata == -9999.0 for dataset in datasets)
        assert all(dataset.compression.name == "deflate" for dataset in datasets)
        assert all(dataset.block_shapes == [(512, 512)] for dataset in datasets)
        assert all(
            dataset.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
            for dataset in datasets
        )
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message="Setting the shape", category=DeprecationWarning
            )
            reference_mask = datasets[0].read_masks(1).copy()
            assert all(
                (dataset.read_masks(1) == reference_mask).all() for dataset in datasets
            )
        assert datasets[0].tags()["QUANTITY"] == "beta0_amplitude"
        assert datasets[0].tags()["POLARIZATION"] == "HH"
        assert datasets[4].tags()["QUANTITY"] == "gamma0_linear_intensity"
        assert datasets[4].tags()["UNITS"] == "1"
        assert datasets[8].tags()["QUANTITY"] == "gamma_nought_calibration_factor"
        assert datasets[8].tags().get("POLARIZATION") is None
    finally:
        for dataset in datasets:
            dataset.close()

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=rasterio.errors.NotGeoreferencedWarning
        )
        with rasterio.open(thumbnail) as dataset:
            assert dataset.driver == "PNG"
            assert dataset.count == 3
            assert dataset.dtypes == ("uint8", "uint8", "uint8")
            assert dataset.colorinterp == (
                rasterio.enums.ColorInterp.red,
                rasterio.enums.ColorInterp.green,
                rasterio.enums.ColorInterp.blue,
            )

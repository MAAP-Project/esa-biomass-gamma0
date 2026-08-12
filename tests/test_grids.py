"""Tests for MGRS grids and geometry-LUT radar windows."""

import numpy as np
from pyproj import Transformer
from rasterio.windows import Window

from esa_biomass_gamma0.calibration import CalibrationMetadata, LutCoordinates
from esa_biomass_gamma0.grids import (
    candidate_grids,
    geometry_coordinates,
    geometry_window,
    target_grid,
)


def test_target_grid_has_exact_northern_and_southern_utm_geometry() -> None:
    """MGRS, rather than parsed IDs, supplies fixed-grid metadata in both hemispheres."""
    northern = target_grid("32TPR")
    southern = target_grid("34HBH")

    assert northern.epsg == 32632
    assert southern.epsg == 32734
    assert northern.bounds == (600_000.0, 5_000_000.0, 700_000.0, 5_100_000.0)
    assert northern.shape == southern.shape == (4000, 4000)
    assert northern.transform.to_gdal() == (
        600_000.0,
        25.0,
        0.0,
        5_100_000.0,
        0.0,
        -25.0,
    )


def test_candidate_grids_use_only_utm_zones_intersecting_the_source_bbox() -> None:
    """An interior bbox cannot create products in neighboring UTM zones."""
    candidates = candidate_grids((10.4, 45.4, 10.6, 45.6))

    assert {grid.tile_id for grid in candidates} == {"32TPR"}
    assert all(grid.shape == (4000, 4000) for grid in candidates)
    assert all(grid.crs.to_epsg() == grid.epsg for grid in candidates)


def test_candidate_grids_retain_zone_and_latitude_band_identifiers() -> None:
    """MGRS round-trips candidates at zone and latitude-band boundaries."""
    zone_boundary = candidate_grids((5.99, 45.4, 6.01, 45.6))
    latitude_band_boundary = candidate_grids((10.4, 47.9, 10.6, 47.95))
    norway = candidate_grids((4.0, 59.5, 4.5, 60.5))
    svalbard = candidate_grids((8.0, 72.5, 8.5, 73.5))

    assert {grid.tile_id for grid in zone_boundary} == {"31TGL", "32TKR"}
    assert {grid.epsg for grid in zone_boundary} == {32631, 32632}
    assert "32TPU" in {grid.tile_id for grid in latitude_band_boundary}
    assert "32UPU" not in {grid.tile_id for grid in latitude_band_boundary}
    assert {grid.epsg for grid in norway} == {32632}
    assert {grid.epsg for grid in svalbard} == {32631}
    assert all(
        target_grid(grid.tile_id) == grid
        for grid in zone_boundary + latitude_band_boundary + norway + svalbard
    )


def test_selects_a_padded_geometry_lut_window_and_samples_it() -> None:
    """A tile maps through LUT geometry to a padded local Beta0 geolocation grid."""
    grid = target_grid("32TPR")
    metadata = CalibrationMetadata(1, 1, (0, 1))
    coordinates = LutCoordinates(
        azimuth=np.arange(100, dtype="float64"),
        slant_range=np.arange(100, dtype="float64"),
        shape=(100, 100),
        dimensions=("azimuth", "range"),
    )
    x, y = np.meshgrid(
        np.linspace(grid.bounds[0], grid.bounds[2], 100),
        np.linspace(grid.bounds[3], grid.bounds[1], 100),
    )
    longitude, latitude = Transformer.from_crs(
        grid.crs, "EPSG:4326", always_xy=True
    ).transform(x, y)

    window = geometry_window(
        longitude,
        latitude,
        grid,
        metadata,
        coordinates,
        100,
        100,
        padding_pixels=8,
    )

    assert window == Window(0, 0, 100, 100)
    sampled_longitude, sampled_latitude = geometry_coordinates(
        longitude, latitude, coordinates, metadata, Window(20, 10, 40, 30)
    )
    assert sampled_longitude.shape == sampled_latitude.shape == (30, 40)
    np.testing.assert_allclose(sampled_longitude, longitude[10:40, 20:60])
    np.testing.assert_allclose(sampled_latitude, latitude[10:40, 20:60])


def test_rejects_nonmatching_or_nonoverlapping_geometry() -> None:
    """Invalid geometry fails and an outside tile is skipped without a GCP fallback."""
    grid = target_grid("32TPR")
    metadata = CalibrationMetadata(1, 1, (0, 1))
    coordinates = LutCoordinates(
        azimuth=np.arange(10, dtype="float64"),
        slant_range=np.arange(10, dtype="float64"),
        shape=(10, 10),
        dimensions=("azimuth", "range"),
    )
    longitude = np.full((10, 10), 0.0)
    latitude = np.full((10, 10), 0.0)

    assert (
        geometry_window(longitude, latitude, grid, metadata, coordinates, 10, 10)
        is None
    )
    with np.testing.assert_raises_regex(ValueError, "shape"):
        geometry_window(
            longitude[:-1], latitude[:-1], grid, metadata, coordinates, 10, 10
        )

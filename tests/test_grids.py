"""Tests for MGRS grids and GCP radar windows."""

import numpy as np
import pytest
from rasterio.control import GroundControlPoint
from rasterio.windows import Window

from esa_biomass_gamma0.grids import (
    candidate_grids,
    gcp_pixel_window,
    shifted_gcps,
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
    assert northern.transform.to_gdal() == (600_000.0, 25.0, 0.0, 5_100_000.0, 0.0, -25.0)


def test_candidate_grids_are_derived_only_from_the_source_bbox() -> None:
    """An AOI-agnostic run retains every MGRS candidate for the source bbox."""
    candidates = candidate_grids((10.4, 45.4, 10.6, 45.6))

    assert candidates
    assert all(grid.shape == (4000, 4000) for grid in candidates)
    assert all(grid.crs.to_epsg() == grid.epsg for grid in candidates)


def test_candidate_grids_retain_zone_and_latitude_band_identifiers() -> None:
    """MGRS round-trips candidates at zone and latitude-band boundaries."""
    zone_boundary = candidate_grids((5.99, 45.4, 6.01, 45.6))
    latitude_band_boundary = candidate_grids((10.4, 47.9, 10.6, 47.95))

    assert {grid.tile_id for grid in zone_boundary} == {"31TGL", "32TKR"}
    assert {grid.epsg for grid in zone_boundary} == {32631, 32632}
    assert "32TPU" in {grid.tile_id for grid in latitude_band_boundary}
    assert "32UPU" not in {grid.tile_id for grid in latitude_band_boundary}
    assert all(target_grid(grid.tile_id) == grid for grid in zone_boundary + latitude_band_boundary)


def test_back_projects_and_clips_a_gcp_window() -> None:
    """A densified perimeter maps through GCPs to the expected local source window."""
    grid = target_grid("32TPR")
    xmin, ymin, xmax, ymax = grid.bounds
    gcps = [
        GroundControlPoint(row=0, col=0, x=xmin, y=ymax),
        GroundControlPoint(row=0, col=99, x=xmax, y=ymax),
        GroundControlPoint(row=99, col=0, x=xmin, y=ymin),
        GroundControlPoint(row=99, col=99, x=xmax, y=ymin),
    ]

    window = gcp_pixel_window(grid, gcps, grid.crs, 100, 100, padding_pixels=8)

    assert window == Window(0, 0, 100, 100)


@pytest.mark.parametrize(
    ("row_limits", "col_limits", "expected"),
    [
        ((-10, 50), (20, 80), Window(12, 0, 77, 59)),
        ((50, 110), (20, 80), Window(12, 42, 77, 58)),
        ((20, 80), (-10, 50), Window(0, 12, 59, 77)),
        ((20, 80), (50, 110), Window(42, 12, 58, 77)),
    ],
)
def test_gcp_window_padding_clips_at_each_raster_edge(
    row_limits: tuple[int, int],
    col_limits: tuple[int, int],
    expected: Window,
) -> None:
    """Padding remains within the source raster at every edge."""
    grid = target_grid("32TPR")
    xmin, ymin, xmax, ymax = grid.bounds
    row_start, row_stop = row_limits
    col_start, col_stop = col_limits
    gcps = [
        GroundControlPoint(row=row_start, col=col_start, x=xmin, y=ymax),
        GroundControlPoint(row=row_start, col=col_stop, x=xmax, y=ymax),
        GroundControlPoint(row=row_stop, col=col_start, x=xmin, y=ymin),
        GroundControlPoint(row=row_stop, col=col_stop, x=xmax, y=ymin),
    ]

    assert gcp_pixel_window(grid, gcps, grid.crs, 100, 100, padding_pixels=8) == expected


def test_rejects_missing_gcps_and_shifts_without_mutating_them() -> None:
    """GCP prerequisites fail clearly and local shifts preserve source controls."""
    grid = target_grid("32TPR")
    with np.testing.assert_raises_regex(ValueError, "GCPs"):
        gcp_pixel_window(grid, [], grid.crs, 100, 100)
    with np.testing.assert_raises_regex(ValueError, "GCPs"):
        gcp_pixel_window(grid, [GroundControlPoint(row=0, col=0, x=0, y=0)], None, 100, 100)

    outside_gcps = [
        GroundControlPoint(row=100, col=100, x=grid.bounds[0], y=grid.bounds[3]),
        GroundControlPoint(row=200, col=200, x=grid.bounds[2], y=grid.bounds[1]),
    ]
    assert gcp_pixel_window(grid, outside_gcps, grid.crs, 100, 100) is None

    non_finite_gcps = [
        GroundControlPoint(row=np.nan, col=np.nan, x=grid.bounds[0], y=grid.bounds[3]),
        GroundControlPoint(row=np.nan, col=np.nan, x=grid.bounds[2], y=grid.bounds[1]),
    ]
    with pytest.warns(RuntimeWarning):
        assert gcp_pixel_window(grid, non_finite_gcps, grid.crs, 100, 100) is None

    original = [GroundControlPoint(row=30, col=40, x=1, y=2)]
    shifted = shifted_gcps(original, Window(20, 10, 5, 5))
    assert (shifted[0].row, shifted[0].col, shifted[0].x, shifted[0].y) == (20, 20, 1, 2)
    assert (original[0].row, original[0].col) == (30, 40)

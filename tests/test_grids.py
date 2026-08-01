"""Tests for MGRS grids and GCP radar windows."""

import numpy as np
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


def test_rejects_missing_gcps_and_shifts_without_mutating_them() -> None:
    """GCP prerequisites fail clearly and local shifts preserve source controls."""
    grid = target_grid("32TPR")
    with np.testing.assert_raises_regex(ValueError, "GCPs"):
        gcp_pixel_window(grid, [], grid.crs, 100, 100)

    original = [GroundControlPoint(row=30, col=40, x=1, y=2)]
    shifted = shifted_gcps(original, Window(20, 10, 5, 5))
    assert (shifted[0].row, shifted[0].col, shifted[0].x, shifted[0].y) == (20, 20, 1, 2)
    assert (original[0].row, original[0].col) == (30, 40)

"""Authoritative MGRS target grids and GCP radar-window selection."""

import math
from dataclasses import dataclass

import numpy as np
from affine import Affine
from mgrs import MGRS
from mgrs.core import MGRSError
from pyproj import Transformer
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.transform import GCPTransformer
from rasterio.windows import Window

MGRS_TILE_SIZE_METERS = 100_000.0
RESOLUTION_METERS = 25.0
MGRS_CONVERTER = MGRS()


@dataclass(frozen=True)
class TileGrid:
    """One exact 100 km MGRS target grid."""

    tile_id: str
    epsg: int
    bounds: tuple[float, float, float, float]
    crs: CRS
    transform: Affine
    shape: tuple[int, int]


def target_grid(tile_id: str) -> TileGrid:
    """Build the exact fixed 25 m UTM target grid for a standard MGRS tile."""
    zone, hemisphere, xmin, ymin = MGRS_CONVERTER.MGRSToUTM(tile_id)
    epsg = (32600 if hemisphere == "N" else 32700) + zone
    bounds = (
        float(xmin),
        float(ymin),
        float(xmin + MGRS_TILE_SIZE_METERS),
        float(ymin + MGRS_TILE_SIZE_METERS),
    )
    shape = (int(MGRS_TILE_SIZE_METERS / RESOLUTION_METERS),) * 2
    return TileGrid(
        tile_id=tile_id,
        epsg=epsg,
        bounds=bounds,
        crs=CRS.from_epsg(epsg),
        transform=Affine.translation(bounds[0], bounds[3])
        * Affine.scale(RESOLUTION_METERS, -RESOLUTION_METERS),
        shape=shape,
    )


def candidate_grids(bbox: tuple[float, float, float, float]) -> list[TileGrid]:
    """Return every MGRS grid whose WGS84 envelope intersects the source bbox."""
    return [target_grid(tile_id) for tile_id in _candidate_tile_ids(bbox)]


def gcp_pixel_window(
    grid: TileGrid,
    gcps: list[GroundControlPoint],
    gcp_crs: CRS | None,
    source_height: int,
    source_width: int,
    padding_pixels: int = 64,
    boundary_spacing_meters: float = 1_000.0,
) -> Window | None:
    """Back-project a densified tile perimeter to a padded, clipped radar window."""
    if not gcps or gcp_crs is None:
        raise ValueError("Beta0 is missing GCPs or a GCP CRS")
    if source_height <= 0 or source_width <= 0 or padding_pixels < 0:
        raise ValueError("source dimensions and padding must be valid")

    boundary = _densified_boundary(grid.bounds, boundary_spacing_meters)
    to_gcp = Transformer.from_crs(grid.crs, gcp_crs, always_xy=True)
    x, y = to_gcp.transform(boundary[:, 0], boundary[:, 1])
    with GCPTransformer(gcps) as transformer:
        rows, cols = transformer.rowcol(x, y)
    rows = np.asarray(rows, dtype="float64")
    cols = np.asarray(cols, dtype="float64")
    valid = np.isfinite(rows) & np.isfinite(cols)
    if not valid.any():
        return None
    rows, cols = rows[valid], cols[valid]
    if (
        rows.max() < 0
        or cols.max() < 0
        or rows.min() >= source_height
        or cols.min() >= source_width
    ):
        return None

    row_start = max(0, math.floor(rows.min()) - padding_pixels)
    row_stop = min(source_height, math.ceil(rows.max()) + padding_pixels + 1)
    col_start = max(0, math.floor(cols.min()) - padding_pixels)
    col_stop = min(source_width, math.ceil(cols.max()) + padding_pixels + 1)
    if row_start >= row_stop or col_start >= col_stop:
        return None
    return Window(col_start, row_start, col_stop - col_start, row_stop - row_start)


def shifted_gcps(
    gcps: list[GroundControlPoint], window: Window
) -> list[GroundControlPoint]:
    """Shift full-image GCP pixel coordinates into a local radar-window frame."""
    return [
        GroundControlPoint(
            row=gcp.row - window.row_off,
            col=gcp.col - window.col_off,
            x=gcp.x,
            y=gcp.y,
            z=gcp.z,
            id=gcp.id,
            info=gcp.info,
        )
        for gcp in gcps
    ]


def _candidate_tile_ids(bbox: tuple[float, float, float, float]) -> list[str]:
    west, south, east, north = bbox
    source_bounds = bbox
    hemispheres = (
        ("S", "N") if south < 0 < north else (("S",) if north <= 0 else ("N",))
    )
    tile_ids: set[str] = set()
    for zone in _intersecting_utm_zones(bbox):
        for hemisphere in hemispheres:
            epsg = (32600 if hemisphere == "N" else 32700) + zone
            to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
            xmin, ymin, xmax, ymax = to_utm.transform_bounds(*bbox, densify_pts=21)
            for easting in range(
                max(100_000, math.floor(xmin / 100_000 - 1) * 100_000),
                min(900_000, math.floor(xmax / 100_000 + 1) * 100_000) + 1,
                100_000,
            ):
                for northing in range(
                    max(0, math.floor(ymin / 100_000 - 1) * 100_000),
                    min(9_900_000, math.floor(ymax / 100_000 + 1) * 100_000) + 1,
                    100_000,
                ):
                    try:
                        tile_id = MGRS_CONVERTER.UTMToMGRS(
                            zone, hemisphere, easting, northing, MGRSPrecision=0
                        )
                        grid = target_grid(tile_id)
                    except MGRSError:
                        continue
                    if grid.epsg == epsg and _intersects(
                        _wgs84_bounds(grid), source_bounds
                    ):
                        tile_ids.add(tile_id)
    return sorted(tile_ids)


def _intersecting_utm_zones(bbox: tuple[float, float, float, float]) -> set[int]:
    west, south, east, north = bbox
    longitudes = {west, east, *range(math.ceil(west), math.floor(east) + 1)}
    latitudes = {south, north, *range(math.ceil(south), math.floor(north) + 1)}
    zones = set()
    for longitude in longitudes:
        for latitude in latitudes:
            tile_id = MGRS_CONVERTER.toMGRS(latitude, longitude, MGRSPrecision=0)
            zone, _, _, _ = MGRS_CONVERTER.MGRSToUTM(tile_id)
            zones.add(zone)
    return zones


def _wgs84_bounds(grid: TileGrid) -> tuple[float, float, float, float]:
    transformer = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    return transformer.transform_bounds(*grid.bounds, densify_pts=21)


def _intersects(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> bool:
    return (
        first[0] <= second[2]
        and first[2] >= second[0]
        and first[1] <= second[3]
        and first[3] >= second[1]
    )


def _densified_boundary(
    bounds: tuple[float, float, float, float], spacing_meters: float
) -> np.ndarray:
    if spacing_meters <= 0:
        raise ValueError("boundary spacing must be positive")
    xmin, ymin, xmax, ymax = bounds
    ring = np.asarray(
        ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)),
        dtype="float64",
    )
    segments = []
    for start, end in zip(ring[:-1], ring[1:], strict=True):
        count = max(1, math.ceil(np.hypot(*(end - start)) / spacing_meters))
        segments.append(start + (end - start) * np.arange(count)[:, None] / count)
    return np.concatenate(segments)

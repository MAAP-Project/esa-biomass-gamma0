"""Authoritative MGRS target grids and geometry-LUT radar-window selection."""

import math
from dataclasses import dataclass

import numpy as np
from affine import Affine
from mgrs import MGRS
from mgrs.core import MGRSError
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.windows import Window
from scipy.ndimage import map_coordinates

from esa_biomass_gamma0.calibration import (
    CalibrationMetadata,
    LutCoordinates,
    lut_pixel_coordinates,
    window_coordinates,
)

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


def geometry_window(
    longitude: np.ndarray,
    latitude: np.ndarray,
    grid: TileGrid,
    metadata: CalibrationMetadata,
    coordinates: LutCoordinates,
    source_height: int,
    source_width: int,
    padding_pixels: int = 64,
) -> Window | None:
    """Return the padded source window whose geometry-LUT nodes cover one tile."""
    if source_height <= 0 or source_width <= 0 or padding_pixels < 0:
        raise ValueError("source dimensions and padding must be valid")
    _validate_geometry(longitude, latitude, coordinates)

    to_grid = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    x, y = to_grid.transform(longitude, latitude)
    within_grid = (
        np.isfinite(x)
        & np.isfinite(y)
        & (x >= grid.bounds[0])
        & (x <= grid.bounds[2])
        & (y >= grid.bounds[1])
        & (y <= grid.bounds[3])
    )
    geometry_rows, geometry_columns = np.nonzero(within_grid)
    if not geometry_rows.size:
        return None

    azimuth, slant_range = window_coordinates(
        metadata, Window(0, 0, source_width, source_height)
    )
    lut_rows, lut_columns = lut_pixel_coordinates(coordinates, azimuth, slant_range)
    if not (np.all(np.diff(lut_rows) > 0) and np.all(np.diff(lut_columns) > 0)):
        raise ValueError("geometry LUT axes do not map monotonically to Beta0 pixels")

    source_rows = np.interp(
        geometry_rows, lut_rows, np.arange(source_height, dtype="float64")
    )
    source_columns = np.interp(
        geometry_columns, lut_columns, np.arange(source_width, dtype="float64")
    )
    row_start = max(0, math.floor(source_rows.min()) - padding_pixels)
    row_stop = min(source_height, math.ceil(source_rows.max()) + padding_pixels + 1)
    column_start = max(0, math.floor(source_columns.min()) - padding_pixels)
    column_stop = min(
        source_width, math.ceil(source_columns.max()) + padding_pixels + 1
    )
    return Window(
        column_start,
        row_start,
        column_stop - column_start,
        row_stop - row_start,
    )


def geometry_coordinates(
    longitude: np.ndarray,
    latitude: np.ndarray,
    coordinates: LutCoordinates,
    metadata: CalibrationMetadata,
    window: Window,
) -> tuple[np.ndarray, np.ndarray]:
    """Bilinearly sample geometry-LUT longitude and latitude onto one window."""
    _validate_geometry(longitude, latitude, coordinates)
    azimuth, slant_range = window_coordinates(metadata, window)
    rows, columns = lut_pixel_coordinates(coordinates, azimuth, slant_range)
    row_coordinates, column_coordinates = np.broadcast_arrays(
        rows[:, np.newaxis], columns[np.newaxis, :]
    )
    sample_coordinates = np.stack((row_coordinates, column_coordinates))
    geolocation = tuple(
        map_coordinates(
            values,
            sample_coordinates,
            order=1,
            mode="nearest",
            prefilter=False,
        ).astype("float64")
        for values in (longitude, latitude)
    )
    if not (np.isfinite(geolocation[0]) & np.isfinite(geolocation[1])).any():
        raise ValueError("geometry LUT has no finite geolocation in the source window")
    return geolocation


def _validate_geometry(
    longitude: np.ndarray, latitude: np.ndarray, coordinates: LutCoordinates
) -> None:
    """Require geometry arrays to align with the validated LUT coordinate axes."""
    if (
        longitude.shape != coordinates.shape
        or latitude.shape != coordinates.shape
        or longitude.ndim != 2
        or latitude.ndim != 2
    ):
        raise ValueError(
            "geometry LUT must match the radiometry (azimuth, range) shape"
        )


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

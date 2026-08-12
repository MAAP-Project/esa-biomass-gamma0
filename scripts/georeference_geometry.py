#!/usr/bin/env python3
"""Warp raw BIOMASS Beta0 using the terrain-aware geometry LUT, not TIFF GCPs."""

import argparse
import logging
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from pyproj import Transformer
from rasterio import open as open_raster
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import Window
from scipy.ndimage import map_coordinates

from esa_biomass_gamma0.calibration import (
    CalibrationMetadata,
    LutCoordinates,
    lut_pixel_coordinates,
    parse_annotation,
    read_lut_coordinates,
    window_coordinates,
)
from esa_biomass_gamma0.grids import TileGrid, target_grid
from esa_biomass_gamma0.raster import NODATA, POLARIZATIONS

logger = logging.getLogger(__name__)


def geometry_window(
    longitude: np.ndarray,
    latitude: np.ndarray,
    grid: TileGrid,
    metadata: CalibrationMetadata,
    coordinates: LutCoordinates,
    source_height: int,
    source_width: int,
    padding_pixels: int,
) -> Window:
    """Return the padded raw-data window covered by one target grid's geometry."""
    if padding_pixels < 0:
        raise ValueError("padding must be non-negative")

    to_grid = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    x, y = to_grid.transform(longitude, latitude)
    within_grid = (
        (x >= grid.bounds[0])
        & (x <= grid.bounds[2])
        & (y >= grid.bounds[1])
        & (y <= grid.bounds[3])
    )
    geometry_rows, geometry_columns = np.nonzero(within_grid)
    if not geometry_rows.size:
        raise ValueError(f"geometry LUT has no coverage in MGRS tile {grid.tile_id}")

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
    row_start = max(0, int(np.floor(source_rows.min())) - padding_pixels)
    row_stop = min(source_height, int(np.ceil(source_rows.max())) + padding_pixels + 1)
    column_start = max(0, int(np.floor(source_columns.min())) - padding_pixels)
    column_stop = min(
        source_width, int(np.ceil(source_columns.max())) + padding_pixels + 1
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
    """Bilinearly interpolate geometry-LUT longitude and latitude onto a window."""
    azimuth, slant_range = window_coordinates(metadata, window)
    rows, columns = lut_pixel_coordinates(coordinates, azimuth, slant_range)
    row_coordinates, column_coordinates = np.broadcast_arrays(
        rows[:, np.newaxis], columns[np.newaxis, :]
    )
    sample_coordinates = np.stack((row_coordinates, column_coordinates))
    return tuple(
        map_coordinates(
            values,
            sample_coordinates,
            order=1,
            mode="nearest",
            prefilter=False,
        )
        for values in (longitude, latitude)
    )


def georeference_beta0(
    beta0_tiff: Path,
    radiometry_lut: Path,
    annotation_xml: Path,
    tile_id: str,
    output: Path,
    *,
    padding_pixels: int = 64,
) -> None:
    """Write one experimental four-band Beta0 COG using LUT geolocation arrays."""
    metadata = parse_annotation(annotation_xml)
    coordinates = read_lut_coordinates(radiometry_lut)
    grid = target_grid(tile_id)

    # ponytail: reads the full geometry grid; process in row chunks if products exceed this POC's memory budget.
    with Dataset(radiometry_lut) as dataset:
        longitude = np.asarray(dataset["geometry/longitude"][:], dtype="float64")
        latitude = np.asarray(dataset["geometry/latitude"][:], dtype="float64")

    with open_raster(beta0_tiff) as source:
        if source.count != len(POLARIZATIONS):
            raise ValueError("Beta0 must contain exactly four polarizations")
        window = geometry_window(
            longitude,
            latitude,
            grid,
            metadata,
            coordinates,
            source.height,
            source.width,
            padding_pixels,
        )
        beta0 = source.read(window=window, out_dtype="float32")
        beta0[source.read_masks(window=window) == 0] = np.nan
        if source.nodata is not None:
            beta0[beta0 == source.nodata] = np.nan

    logger.info("Using LUT-selected source window %s", window)
    geolocation = geometry_coordinates(
        longitude, latitude, coordinates, metadata, window
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with open_raster(
        output,
        "w",
        driver="COG",
        height=grid.shape[0],
        width=grid.shape[1],
        count=len(POLARIZATIONS),
        dtype="float32",
        crs=grid.crs,
        transform=grid.transform,
        nodata=NODATA,
        blocksize=512,
        compress="DEFLATE",
    ) as destination:
        for index, polarization in enumerate(POLARIZATIONS, start=1):
            warped = np.full(grid.shape, np.nan, dtype="float32")
            reproject(
                source=beta0[index - 1],
                destination=warped,
                src_geoloc_array=geolocation,
                src_crs=CRS.from_epsg(4326),
                src_nodata=np.nan,
                dst_crs=grid.crs,
                dst_transform=grid.transform,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
                num_threads=4,
            )
            valid_pixels = int(np.isfinite(warped).sum())
            if not valid_pixels:
                raise ValueError(f"geometry-LUT warp produced no {polarization} data")
            destination.write(np.where(np.isfinite(warped), warped, NODATA), index)
            destination.set_band_description(index, f"Beta0 amplitude {polarization}")
            logger.info("Warped %s: %d valid pixels", polarization, valid_pixels)
        destination.update_tags(
            EXPERIMENTAL="geometry-LUT georeferencing; not a production Gamma0 asset",
            GEOLOCATION_SOURCE="geometry/longitude,geometry/latitude",
        )


def main() -> None:
    """Parse command-line arguments and run the geometry-LUT experiment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("beta0_tiff", type=Path)
    parser.add_argument("radiometry_lut", type=Path)
    parser.add_argument("annotation_xml", type=Path)
    parser.add_argument(
        "tile_id", help="Standard 100 km MGRS tile ID, for example 49VCJ"
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--padding-pixels", type=int, default=64)
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(message)s")
    georeference_beta0(
        args.beta0_tiff,
        args.radiometry_lut,
        args.annotation_xml,
        args.tile_id,
        args.output,
        padding_pixels=args.padding_pixels,
    )


if __name__ == "__main__":
    main()

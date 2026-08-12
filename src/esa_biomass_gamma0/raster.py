"""Direct geometry-LUT warps and validated raster assets for one MGRS tile."""

from pathlib import Path

import numpy as np
from rasterio import open as open_raster
from rasterio.crs import CRS
from rasterio.enums import ColorInterp, Resampling
from rasterio.warp import reproject

from esa_biomass_gamma0.grids import TileGrid

NODATA = np.float32(-9999.0)
POLARIZATIONS = ("HH", "HV", "VH", "VV")
THUMBNAIL_POLARIZATIONS = ("HH", "HV", "VV")


def product_asset_filename(key: str, source_item_id: str, tile_id: str) -> str:
    """Return a job-unique product filename for one asset key."""
    extension = ".png" if key == "thumbnail" else ".tif"
    return f"{source_item_id}-{tile_id}-{key}{extension}"


def warp_scientific_arrays(
    beta0: np.ndarray,
    gamma0: np.ndarray,
    gamma_nought: np.ndarray,
    geolocation: tuple[np.ndarray, np.ndarray],
    grid: TileGrid,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Warp local scientific arrays from geometry-LUT coordinates onto one tile."""
    beta0, gamma0, gamma_nought = _validated_scientific_arrays(
        beta0, gamma0, gamma_nought
    )
    longitude, latitude = _validated_geolocation(geolocation, beta0.shape[-2:])

    warped_beta0 = _warp_stack(beta0, longitude, latitude, grid)
    warped_gamma0 = _warp_stack(gamma0, longitude, latitude, grid)
    warped_gamma_nought = _warp_array(gamma_nought, longitude, latitude, grid)

    if not any(np.isfinite(array).any() for array in (warped_beta0, warped_gamma0)):
        return None

    return warped_beta0, warped_gamma0, warped_gamma_nought


def write_scientific_cogs(
    directory: Path,
    grid: TileGrid,
    beta0: np.ndarray,
    gamma0: np.ndarray,
    gamma_nought: np.ndarray,
    *,
    source_item_id: str,
    processing_version: str,
) -> dict[str, Path]:
    """Write and validate the nine single-band scientific COGs in a staged directory."""
    if not directory.is_dir():
        raise ValueError(f"staged tile directory does not exist: {directory}")

    if not source_item_id or not processing_version:
        raise ValueError("source item ID and processing version are required")

    beta0, gamma0, gamma_nought = _validated_scientific_arrays(
        beta0, gamma0, gamma_nought
    )
    baseline_valid = np.isfinite(beta0).all(axis=0)
    beta0 = np.where(baseline_valid, beta0, np.nan)
    gamma0 = np.where(baseline_valid, gamma0, np.nan)
    gamma_nought = np.where(baseline_valid, gamma_nought, np.nan)

    if beta0.shape[-2:] != grid.shape:
        raise ValueError("scientific array shape does not match the target grid")

    assets: list[tuple[str, np.ndarray, str, str | None]] = [
        *(
            (
                f"beta0_{polarization.lower()}",
                beta0[index],
                "beta0_amplitude",
                polarization,
            )
            for index, polarization in enumerate(POLARIZATIONS)
        ),
        *(
            (
                f"gamma0_{polarization.lower()}",
                gamma0[index],
                "gamma0_linear_intensity",
                polarization,
            )
            for index, polarization in enumerate(POLARIZATIONS)
        ),
        ("gamma0_lut", gamma_nought, "gamma_nought_calibration_factor", None),
    ]

    paths: dict[str, Path] = {}

    for key, data, quantity, polarization in assets:
        path = directory / product_asset_filename(key, source_item_id, grid.tile_id)
        _write_cog(
            path,
            data,
            grid,
            quantity=quantity,
            polarization=polarization,
            source_item_id=source_item_id,
            processing_version=processing_version,
        )
        validate_scientific_cog(
            path,
            grid,
            quantity=quantity,
            polarization=polarization,
            source_item_id=source_item_id,
            processing_version=processing_version,
        )
        paths[key] = path

    return paths


def validate_scientific_cog(
    path: Path,
    grid: TileGrid,
    *,
    quantity: str,
    polarization: str | None,
    source_item_id: str,
    processing_version: str,
) -> None:
    """Raise ``ValueError`` unless one scientific asset satisfies the COG contract."""
    try:
        with open_raster(path) as dataset:
            if dataset.driver != "GTiff":
                raise ValueError("not a GeoTIFF")
            if dataset.count != 1 or dataset.dtypes != ("float32",):
                raise ValueError("must contain one float32 band")
            if dataset.crs != grid.crs or dataset.transform != grid.transform:
                raise ValueError("grid CRS or transform differs from the target")
            if (dataset.height, dataset.width) != grid.shape:
                raise ValueError("grid shape differs from the target")
            if dataset.nodata != NODATA:
                raise ValueError("nodata must be -9999.0")
            if dataset.compression is None or dataset.compression.name != "deflate":
                raise ValueError("compression must be DEFLATE")
            if dataset.block_shapes != [(512, 512)]:
                raise ValueError("block size must be 512 pixels")
            if dataset.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") != "COG":
                raise ValueError("GeoTIFF is not a COG")
            tags = dataset.tags()

    except (OSError, ValueError) as error:
        raise ValueError(
            f"scientific COG validation failed for {path}: {error}"
        ) from error

    expected_tags = {
        "QUANTITY": quantity,
        "UNITS": "1",
        "SOURCE_ITEM_ID": source_item_id,
        "PROCESSING_VERSION": processing_version,
    }

    if polarization is not None:
        expected_tags["POLARIZATION"] = polarization

    for name, value in expected_tags.items():
        if tags.get(name) != value:
            raise ValueError(
                f"scientific COG validation failed for {path}: missing {name} tag"
            )

    if polarization is None and "POLARIZATION" in tags:
        raise ValueError(
            f"scientific COG validation failed for {path}: unexpected POLARIZATION tag"
        )


def write_thumbnail(path: Path, gamma0: np.ndarray) -> Path:
    """Write and validate a display-only HH/HV/VV Gamma0 RGB thumbnail."""
    gamma0 = np.asarray(gamma0, dtype="float32")

    if gamma0.ndim != 3 or gamma0.shape[0] != len(POLARIZATIONS):
        raise ValueError("Gamma0 thumbnail requires four polarizations")

    if not path.parent.is_dir():
        raise ValueError(f"thumbnail directory does not exist: {path.parent}")

    channels = tuple(
        POLARIZATIONS.index(polarization) for polarization in THUMBNAIL_POLARIZATIONS
    )

    rgb = np.stack([_display_channel(gamma0[index]) for index in channels])

    with open_raster(
        path,
        "w",
        driver="PNG",
        height=gamma0.shape[1],
        width=gamma0.shape[2],
        count=3,
        dtype="uint8",
    ) as dataset:
        dataset.write(rgb)
        dataset.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue)

    validate_thumbnail(path)

    return path


def validate_thumbnail(path: Path) -> None:
    """Raise ``ValueError`` unless a thumbnail is a three-band display-only PNG."""
    try:
        with open_raster(path) as dataset:
            if (
                dataset.driver != "PNG"
                or dataset.count != 3
                or dataset.dtypes != ("uint8",) * 3
            ):
                raise ValueError("must be a three-band uint8 PNG")
            if dataset.colorinterp != (
                ColorInterp.red,
                ColorInterp.green,
                ColorInterp.blue,
            ):
                raise ValueError("must use RGB color interpretation")
    except (OSError, ValueError) as error:
        raise ValueError(f"thumbnail validation failed for {path}: {error}") from error


def _validated_scientific_arrays(
    beta0: np.ndarray, gamma0: np.ndarray, gamma_nought: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return float32 arrays after enforcing the scientific window-shape invariants."""
    beta0 = np.asarray(beta0, dtype="float32")
    gamma0 = np.asarray(gamma0, dtype="float32")
    gamma_nought = np.asarray(gamma_nought, dtype="float32")

    if beta0.ndim != 3 or beta0.shape[0] != len(POLARIZATIONS):
        raise ValueError("Beta0 must contain four polarizations")

    if gamma0.shape != beta0.shape:
        raise ValueError("Beta0 and Gamma0 must have the same shape")

    if gamma_nought.shape != beta0.shape[-2:]:
        raise ValueError("GammaNought must match the Beta0 window shape")

    return beta0, gamma0, gamma_nought


def _validated_geolocation(
    geolocation: tuple[np.ndarray, np.ndarray], shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """Return matching longitude and latitude arrays with usable source coverage."""
    if len(geolocation) != 2:
        raise ValueError("geolocation requires longitude and latitude arrays")
    longitude, latitude = (np.asarray(values, dtype="float64") for values in geolocation)
    if longitude.shape != shape or latitude.shape != shape:
        raise ValueError("geolocation must match the scientific source window shape")
    if not (np.isfinite(longitude) & np.isfinite(latitude)).any():
        raise ValueError("geolocation has no finite source coverage")
    return longitude, latitude


def _warp_stack(
    data: np.ndarray, longitude: np.ndarray, latitude: np.ndarray, grid: TileGrid
) -> np.ndarray:
    """Directly warp every polarization in one radar-space stack."""
    return np.stack(
        [_warp_array(band, longitude, latitude, grid) for band in data]
    )


def _warp_array(
    data: np.ndarray, longitude: np.ndarray, latitude: np.ndarray, grid: TileGrid
) -> np.ndarray:
    """Directly bilinearly warp one geometry-LUT source array to one tile grid."""
    destination = np.full(grid.shape, np.nan, dtype="float32")

    reproject(
        source=data,
        destination=destination,
        src_geoloc_array=(longitude, latitude),
        src_crs=CRS.from_epsg(4326),
        dst_crs=grid.crs,
        dst_transform=grid.transform,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return destination


def _write_cog(
    path: Path,
    data: np.ndarray,
    grid: TileGrid,
    *,
    quantity: str,
    polarization: str | None,
    source_item_id: str,
    processing_version: str,
) -> None:
    """Write one final-nodata scientific COG without an intermediate raster."""
    output = np.where(np.isfinite(data), data, NODATA).astype("float32")
    with open_raster(
        path,
        "w",
        driver="COG",
        height=grid.shape[0],
        width=grid.shape[1],
        count=1,
        dtype="float32",
        crs=grid.crs,
        transform=grid.transform,
        nodata=NODATA,
        blocksize=512,
        compress="DEFLATE",
    ) as dataset:
        dataset.write(output, 1)
        tags = {
            "QUANTITY": quantity,
            "UNITS": "1",
            "SOURCE_ITEM_ID": source_item_id,
            "PROCESSING_VERSION": processing_version,
        }

        if polarization is not None:
            tags["POLARIZATION"] = polarization

        dataset.update_tags(**tags)


def _display_channel(data: np.ndarray) -> np.ndarray:
    """Apply the notebook's deterministic 2nd-to-98th percentile display stretch."""
    output = np.zeros(data.shape, dtype="uint8")
    valid = np.isfinite(data)

    if not valid.any():
        return output

    low, high = np.percentile(data[valid], (2, 98))
    if high <= low:
        output[valid] = 255
        return output

    output[valid] = np.clip((data[valid] - low) / (high - low) * 255, 0, 255).astype(
        "uint8"
    )

    return output

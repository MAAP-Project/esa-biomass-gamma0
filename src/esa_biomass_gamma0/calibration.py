"""Physical-coordinate radiometry LUT sampling and Gamma0 calculation."""

import math
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
from netCDF4 import Dataset
from rasterio.windows import Window
from scipy.ndimage import map_coordinates


@dataclass(frozen=True)
class CalibrationMetadata:
    """Annotation values that map Beta0 pixels into LUT coordinates."""

    azimuth_interval: float
    range_pixel_spacing: float
    ground_to_slant_coefficients: tuple[float, ...]


@dataclass(frozen=True)
class LutCoordinates:
    """Validated physical coordinate vectors for a gammaNought LUT."""

    azimuth: np.ndarray
    slant_range: np.ndarray
    shape: tuple[int, int]


def parse_annotation(annotation_xml: bytes | Path) -> CalibrationMetadata:
    """Read the annotation values needed to calibrate Beta0 amplitudes."""
    document = (
        annotation_xml.read_bytes()
        if isinstance(annotation_xml, Path)
        else annotation_xml
    )

    try:
        sar_image = ElementTree.fromstring(document).find("sarImage")
    except ElementTree.ParseError as error:
        raise ValueError(f"annotation_xml: invalid XML: {error}") from error

    if sar_image is None:
        raise ValueError("annotation_xml: missing sarImage")

    azimuth_interval = _required_float(sar_image, "azimuthTimeInterval")
    range_pixel_spacing = _required_float(sar_image, "rangePixelSpacing")
    coefficients_text = sar_image.findtext(
        "rangeCoordinateConversion/coordinateConversion/groundToSlantCoefficients"
    )

    if not coefficients_text or not coefficients_text.split():
        raise ValueError("annotation_xml: missing groundToSlantCoefficients")

    try:
        coefficients = tuple(float(value) for value in coefficients_text.split())
    except ValueError as error:
        raise ValueError("annotation_xml: invalid groundToSlantCoefficients") from error

    if not all(math.isfinite(value) for value in coefficients):
        raise ValueError("annotation_xml: invalid groundToSlantCoefficients")

    return CalibrationMetadata(
        azimuth_interval=azimuth_interval,
        range_pixel_spacing=range_pixel_spacing,
        ground_to_slant_coefficients=coefficients,
    )


def read_lut_coordinates(lut_path: Path) -> LutCoordinates:
    """Read and validate gammaNought LUT axes without reading its full data array."""
    with Dataset(lut_path) as dataset:
        try:
            azimuth_variable = dataset.variables["relativeAzimuthTimeRGC"]
            range_variable = dataset.variables["slantRangeTimeRGC"]
            gamma_nought = _gamma_nought_variable(dataset)
        except KeyError as error:
            raise ValueError(f"radiometry LUT: missing {error.args[0]}") from error

        azimuth = _coordinate_vector(azimuth_variable[:], "relativeAzimuthTimeRGC")
        slant_range = _coordinate_vector(range_variable[:], "slantRangeTimeRGC")
        expected_dimensions = (
            azimuth_variable.dimensions[0],
            range_variable.dimensions[0],
        )

        if gamma_nought.dimensions != expected_dimensions:
            raise ValueError(
                "radiometry LUT: gammaNought dimensions must be (azimuth, range)"
            )

        shape = tuple(int(size) for size in gamma_nought.shape)

        if shape != (azimuth.size, slant_range.size):
            raise ValueError(
                "radiometry LUT: gammaNought shape does not match coordinate vectors"
            )

    azimuth.setflags(write=False)
    slant_range.setflags(write=False)

    return LutCoordinates(azimuth=azimuth, slant_range=slant_range, shape=shape)


def window_coordinates(
    metadata: CalibrationMetadata, window: Window
) -> tuple[np.ndarray, np.ndarray]:
    """Return physical azimuth and slant-range coordinates for a Beta0 window."""
    row_offset, height = _window_axis(window.row_off, window.height, "row")
    column_offset, width = _window_axis(window.col_off, window.width, "column")
    rows = np.arange(row_offset, row_offset + height, dtype="float64")
    columns = np.arange(column_offset, column_offset + width, dtype="float64")

    return (
        rows * metadata.azimuth_interval,
        np.polynomial.polynomial.polyval(
            columns * metadata.range_pixel_spacing,
            metadata.ground_to_slant_coefficients,
        ),
    )


def lut_pixel_coordinates(
    coordinates: LutCoordinates,
    azimuth: np.ndarray,
    slant_range: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Map physical source coordinates to fractional gammaNought LUT pixels."""
    azimuth = _sample_axis(azimuth, "azimuth")
    slant_range = _sample_axis(slant_range, "range")

    _require_covered(azimuth, coordinates.azimuth, "azimuth")
    _require_covered(slant_range, coordinates.slant_range, "range")

    return (
        np.interp(
            azimuth,
            coordinates.azimuth,
            np.arange(coordinates.azimuth.size, dtype="float64"),
        ),
        np.interp(
            slant_range,
            coordinates.slant_range,
            np.arange(coordinates.slant_range.size, dtype="float64"),
        ),
    )


def sample_gamma_nought(
    lut_path: Path,
    coordinates: LutCoordinates,
    azimuth: np.ndarray,
    slant_range: np.ndarray,
) -> np.ndarray:
    """Read one bracketed gammaNought slice and sample it onto source pixels."""
    lut_rows, lut_columns = lut_pixel_coordinates(coordinates, azimuth, slant_range)
    row_start, row_stop = _bracket(lut_rows, coordinates.shape[0])
    column_start, column_stop = _bracket(lut_columns, coordinates.shape[1])

    with Dataset(lut_path) as dataset:
        try:
            gamma_nought = _gamma_nought_variable(dataset)
        except KeyError as error:
            raise ValueError("radiometry LUT: missing gammaNought") from error

        lut = np.asarray(
            gamma_nought[row_start:row_stop, column_start:column_stop], dtype="float32"
        )

    return resample_gamma_nought(
        lut,
        lut_rows - row_start,
        lut_columns - column_start,
    )


def resample_gamma_nought(
    lut: np.ndarray, lut_rows: np.ndarray, lut_columns: np.ndarray
) -> np.ndarray:
    """Bilinearly sample a gammaNought array at fractional LUT rows and columns."""
    lut = np.asarray(lut, dtype="float32")
    if lut.ndim != 2:
        raise ValueError("gammaNought LUT must have azimuth and range dimensions")

    rows, columns = np.broadcast_arrays(
        lut_rows[:, np.newaxis], lut_columns[np.newaxis, :]
    )

    return map_coordinates(
        lut,
        np.stack((rows, columns)),
        order=1,
        mode="nearest",
        prefilter=False,
    ).astype("float32")


def calculate_gamma0(beta0: np.ndarray, gamma_nought: np.ndarray) -> np.ndarray:
    """Convert Beta0 amplitudes to linear Gamma0 while preserving missing values."""
    beta0 = np.asarray(beta0, dtype="float32")
    gamma_nought = np.asarray(gamma_nought, dtype="float32")

    if beta0.ndim < 2 or gamma_nought.shape != beta0.shape[-2:]:
        raise ValueError("Beta0 and gammaNought shapes are incompatible")

    return beta0**2 * gamma_nought


def _required_float(element: ElementTree.Element, field: str) -> float:
    """Read one positive finite scalar annotation field."""
    text = element.findtext(field)
    if not text:
        raise ValueError(f"annotation_xml: missing {field}")

    try:
        value = float(text)
    except ValueError as error:
        raise ValueError(f"annotation_xml: invalid {field}") from error

    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"annotation_xml: invalid {field}")

    return value


def _gamma_nought_variable(dataset: Dataset) -> object:
    """Return the required radiometry variable with one stable missing-field error."""
    try:
        return dataset.groups["radiometry"].variables["gammaNought"]
    except KeyError as error:
        raise KeyError("gammaNought") from error


def _coordinate_vector(values: np.ndarray, name: str) -> np.ndarray:
    """Validate one monotonic physical coordinate vector."""
    values = np.asarray(values, dtype="float64")
    if values.ndim != 1 or values.size < 2:
        raise ValueError(f"radiometry LUT: {name} must be a one-dimensional vector")

    if not np.isfinite(values).all() or not np.all(np.diff(values) > 0):
        raise ValueError(
            f"radiometry LUT: {name} must be finite and strictly monotonic"
        )

    return values


def _window_axis(offset: float, length: float, name: str) -> tuple[int, int]:
    """Validate a whole-pixel window axis and return its integer range."""
    if not all(math.isfinite(value) for value in (offset, length)):
        raise ValueError(f"Beta0 window {name} values must be finite")

    if not offset.is_integer() or not length.is_integer() or length <= 0:
        raise ValueError(f"Beta0 window {name} values must be positive whole pixels")

    return int(offset), int(length)


def _sample_axis(values: np.ndarray, name: str) -> np.ndarray:
    """Validate one non-empty finite source-coordinate axis."""
    values = np.asarray(values, dtype="float64")

    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError(f"Beta0 {name} coordinates must be finite and one-dimensional")

    return values


def _require_covered(values: np.ndarray, lut_values: np.ndarray, name: str) -> None:
    """Reject source coordinates that would require LUT extrapolation."""
    if values.min() < lut_values[0] or values.max() > lut_values[-1]:
        raise ValueError(f"Beta0 {name} extent falls outside the LUT")


def _bracket(values: np.ndarray, size: int) -> tuple[int, int]:
    """Return the one-cell-padded LUT slice bounds for fractional indices."""
    return (
        max(0, int(np.floor(values.min())) - 1),
        min(size, int(np.ceil(values.max())) + 2),
    )

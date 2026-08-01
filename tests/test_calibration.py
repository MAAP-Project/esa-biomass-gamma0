"""Tests for physical-coordinate Gamma0 calibration."""

from pathlib import Path
import warnings

from netCDF4 import Dataset
import numpy as np
import pytest
from rasterio.windows import Window

from esa_biomass_gamma0.calibration import (
    calculate_gamma0,
    parse_annotation,
    read_lut_coordinates,
    sample_gamma_nought,
    window_coordinates,
)


def _write_annotation(path: Path, *, body: str | None = None) -> None:
    """Write a minimal BIOMASS annotation document."""
    path.write_text(
        body
        or """<product><sarImage>
        <azimuthTimeInterval>10</azimuthTimeInterval>
        <rangePixelSpacing>5</rangePixelSpacing>
        <rangeCoordinateConversion><coordinateConversion>
        <groundToSlantCoefficients>0 1</groundToSlantCoefficients>
        </coordinateConversion></rangeCoordinateConversion>
        </sarImage></product>""",
        encoding="utf-8",
    )


def _write_lut(
    path: Path,
    *,
    azimuth: tuple[float, ...] = (0, 10, 20, 30, 40, 50),
    slant_range: tuple[float, ...] = (0, 10, 20, 30, 40, 50),
    gamma_dimensions: tuple[str, str] = ("azimuth", "range"),
) -> None:
    """Write a small LUT whose values identify their physical axis locations."""
    with Dataset(path, "w") as dataset:
        dataset.createDimension("azimuth", len(azimuth))
        dataset.createDimension("range", len(slant_range))
        azimuth_variable = dataset.createVariable("relativeAzimuthTimeRGC", "f8", ("azimuth",))
        range_variable = dataset.createVariable("slantRangeTimeRGC", "f8", ("range",))
        azimuth_variable[:] = azimuth
        range_variable[:] = slant_range
        radiometry = dataset.createGroup("radiometry")
        gamma_nought = radiometry.createVariable("gammaNought", "f4", gamma_dimensions)
        values = (
            np.arange(len(azimuth), dtype="float32")[:, None] * 100
            + np.arange(len(slant_range), dtype="float32")[None, :] * 10
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            gamma_nought[:, :] = values if gamma_dimensions == ("azimuth", "range") else values.T


def test_samples_a_bracketed_lut_window_in_physical_coordinates(tmp_path: Path) -> None:
    """A non-zero radar window samples the corresponding LUT nodes and midpoints."""
    annotation_path = tmp_path / "annotation.xml"
    lut_path = tmp_path / "radiometry.nc"
    _write_annotation(annotation_path)
    _write_lut(lut_path)

    metadata = parse_annotation(annotation_path)
    coordinates = read_lut_coordinates(lut_path)
    azimuth, slant_range = window_coordinates(metadata, Window(2, 3, 2, 2))
    sampled = sample_gamma_nought(lut_path, coordinates, azimuth, slant_range)

    assert azimuth.tolist() == [30.0, 40.0]
    assert slant_range.tolist() == [10.0, 15.0]
    np.testing.assert_allclose(sampled, [[310.0, 315.0], [410.0, 415.0]])


def test_windowed_sampling_matches_the_full_frame_slice(tmp_path: Path) -> None:
    """Windowed interpolation has the same values as its full-frame counterpart."""
    annotation_path = tmp_path / "annotation.xml"
    lut_path = tmp_path / "radiometry.nc"
    _write_annotation(annotation_path)
    _write_lut(lut_path)
    metadata = parse_annotation(annotation_path)
    coordinates = read_lut_coordinates(lut_path)

    full_azimuth, full_range = window_coordinates(metadata, Window(0, 0, 6, 6))
    window_azimuth, window_range = window_coordinates(metadata, Window(2, 3, 2, 2))
    full = sample_gamma_nought(lut_path, coordinates, full_azimuth, full_range)
    windowed = sample_gamma_nought(lut_path, coordinates, window_azimuth, window_range)

    np.testing.assert_allclose(windowed, full[3:5, 2:4], atol=1e-3)


@pytest.mark.parametrize(
    "document, message",
    [
        ("<product />", "sarImage"),
        ("<product><sarImage /></product>", "azimuthTimeInterval"),
        (
            """<product><sarImage><azimuthTimeInterval>not-a-number</azimuthTimeInterval>
            </sarImage></product>""",
            "azimuthTimeInterval",
        ),
        (
            """<product><sarImage><azimuthTimeInterval>1</azimuthTimeInterval>
            <rangePixelSpacing>1</rangePixelSpacing><rangeCoordinateConversion>
            <coordinateConversion><groundToSlantCoefficients>bad</groundToSlantCoefficients>
            </coordinateConversion></rangeCoordinateConversion></sarImage></product>""",
            "groundToSlantCoefficients",
        ),
    ],
)
def test_rejects_incomplete_or_invalid_annotation_fields(
    tmp_path: Path, document: str, message: str
) -> None:
    """Annotation failures name the required field that cannot be used."""
    annotation_path = tmp_path / "annotation.xml"
    _write_annotation(annotation_path, body=document)

    with pytest.raises(ValueError, match=message):
        parse_annotation(annotation_path)


@pytest.mark.parametrize(
    "azimuth, slant_range, gamma_dimensions, message",
    [
        ((0, 20, 10), (0, 10, 20), ("azimuth", "range"), "monotonic"),
        ((0, 10, 20), (0, 10, 20), ("range", "azimuth"), "dimensions"),
    ],
)
def test_rejects_invalid_lut_axes(
    tmp_path: Path,
    azimuth: tuple[float, ...],
    slant_range: tuple[float, ...],
    gamma_dimensions: tuple[str, str],
    message: str,
) -> None:
    """LUT coordinates and dimensions must support azimuth-range interpolation."""
    lut_path = tmp_path / "radiometry.nc"
    _write_lut(
        lut_path,
        azimuth=azimuth,
        slant_range=slant_range,
        gamma_dimensions=gamma_dimensions,
    )

    with pytest.raises(ValueError, match=message):
        read_lut_coordinates(lut_path)


def test_rejects_gamma_nought_with_an_extra_dimension(tmp_path: Path) -> None:
    """A three-dimensional LUT cannot be treated as an azimuth-range surface."""
    lut_path = tmp_path / "radiometry.nc"
    with Dataset(lut_path, "w") as dataset:
        dataset.createDimension("azimuth", 2)
        dataset.createDimension("range", 2)
        dataset.createDimension("layer", 1)
        dataset.createVariable("relativeAzimuthTimeRGC", "f8", ("azimuth",))[:] = [0, 1]
        dataset.createVariable("slantRangeTimeRGC", "f8", ("range",))[:] = [0, 1]
        dataset.createGroup("radiometry").createVariable(
            "gammaNought", "f4", ("azimuth", "range", "layer")
        )

    with pytest.raises(ValueError, match="dimensions"):
        read_lut_coordinates(lut_path)


def test_rejects_missing_gamma_nought_variable(tmp_path: Path) -> None:
    """The required radiometry variable cannot be silently substituted."""
    lut_path = tmp_path / "radiometry.nc"
    with Dataset(lut_path, "w") as dataset:
        dataset.createDimension("azimuth", 2)
        dataset.createDimension("range", 2)
        dataset.createVariable("relativeAzimuthTimeRGC", "f8", ("azimuth",))[:] = [0, 1]
        dataset.createVariable("slantRangeTimeRGC", "f8", ("range",))[:] = [0, 1]

    with pytest.raises(ValueError, match="gammaNought"):
        read_lut_coordinates(lut_path)


def test_rejects_windows_outside_the_lut_and_preserves_gamma0_nan() -> None:
    """Calibration refuses extrapolation while retaining scientific nodata."""
    beta = np.array([[[2.0, np.nan, 2.0]]], dtype="float64")
    lut = np.array([[3.0, 3.0, np.nan]], dtype="float64")

    gamma0 = calculate_gamma0(beta, lut)

    assert gamma0.dtype == np.float32
    assert gamma0[0, 0, 0] == 12.0
    assert np.isnan(gamma0[0, 0, 1])
    assert np.isnan(gamma0[0, 0, 2])


def test_rejects_lut_sampling_outside_physical_extent(tmp_path: Path) -> None:
    """Physical source coordinates outside a LUT fail instead of clamping."""
    lut_path = tmp_path / "radiometry.nc"
    _write_lut(lut_path)
    coordinates = read_lut_coordinates(lut_path)

    with pytest.raises(ValueError, match="azimuth"):
        sample_gamma_nought(lut_path, coordinates, np.array([60.0]), np.array([10.0]))
    with pytest.raises(ValueError, match="range"):
        sample_gamma_nought(lut_path, coordinates, np.array([10.0]), np.array([60.0]))

"""Tests for local staged-source validation."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import write_item
from esa_biomass_gamma0.source import validate_staged_source


def test_validates_local_source_and_sanitizes_provenance(
    staged_paths: dict[str, Path],
) -> None:
    """Only staged paths are opened while remote provenance is retained safely."""
    write_item(staged_paths["source_item"])

    source = validate_staged_source(**staged_paths)

    assert source.item_id == "BIOMASS_TEST_001"
    assert source.collection_id == "BiomassLevel1b"
    assert source.datetime == datetime(2026, 7, 31, 12, tzinfo=timezone.utc)
    assert source.bbox == (10.0, 45.0, 11.0, 46.0)
    assert source.self_href == "https://example.test/items/test.json"
    assert source.asset_hrefs == {
        "enclosure_tiff": "https://example.test/beta.tif",
        "enclosure_nc": "https://example.test/lut.nc",
        "enclosure_annot_xml": "https://example.test/annotation.xml",
    }
    assert "secret" not in repr(source)


def test_normalizes_three_dimensional_bbox(staged_paths: dict[str, Path]) -> None:
    """STAC 3D bboxes retain their horizontal component."""
    write_item(staged_paths["source_item"], bbox=[10.0, 45.0, 0.0, 11.0, 46.0, 2.0])

    source = validate_staged_source(**staged_paths)

    assert source.bbox == (10.0, 45.0, 11.0, 46.0)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"id": ""}, "id"),
        ({"collection": None}, "collection"),
        ({"properties": {}}, "datetime"),
        ({"bbox": [10.0, 45.0, 10.0, 46.0]}, "bbox"),
        ({"bbox": [179.0, 45.0, -179.0, 46.0]}, "antimeridian"),
        ({"bbox": [10.0, 84.0, 11.0, 85.0]}, "polar"),
        ({"assets": {}}, "enclosure_tiff"),
    ],
)
def test_rejects_invalid_item_metadata(
    staged_paths: dict[str, Path], change: dict[str, object], message: str
) -> None:
    """Invalid source identity, coverage, and required assets fail locally."""
    write_item(staged_paths["source_item"], **change)

    with pytest.raises(ValueError, match=message):
        validate_staged_source(**staged_paths)


def test_rejects_missing_or_non_regular_staged_file(
    staged_paths: dict[str, Path],
) -> None:
    """Every staged input must be a readable regular file."""
    write_item(staged_paths["source_item"])
    staged_paths["beta0_tiff"].unlink()

    with pytest.raises(ValueError, match="beta0_tiff"):
        validate_staged_source(**staged_paths)

    staged_paths["beta0_tiff"].mkdir()
    with pytest.raises(ValueError, match="beta0_tiff"):
        validate_staged_source(**staged_paths)


def test_rejects_malformed_item_json(staged_paths: dict[str, Path]) -> None:
    """Source Item parsing reports its local JSON path."""
    staged_paths["source_item"].write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="source_item"):
        validate_staged_source(**staged_paths)

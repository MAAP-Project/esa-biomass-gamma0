"""Shared staged-input builders."""

import json
from pathlib import Path

import pytest


@pytest.fixture
def staged_paths(tmp_path: Path) -> dict[str, Path]:
    """Create the four readable paths required by the staged-source boundary."""
    paths = {
        "source_item": tmp_path / "source-item.json",
        "beta0_tiff": tmp_path / "beta0.tif",
        "radiometry_lut": tmp_path / "radiometry.nc",
        "annotation_xml": tmp_path / "annotation.xml",
    }
    for path in paths.values():
        path.write_bytes(b"staged input")
    return paths


def write_item(path: Path, **overrides: object) -> None:
    """Write a minimal local STAC Item with BIOMASS source-asset URLs."""
    item: dict[str, object] = {
        "stac_version": "1.0.0",
        "type": "Feature",
        "id": "BIOMASS_TEST_001",
        "collection": "BiomassLevel1b",
        "bbox": [10.0, 45.0, 11.0, 46.0],
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[10.0, 45.0], [11.0, 45.0], [11.0, 46.0], [10.0, 46.0], [10.0, 45.0]]
            ],
        },
        "properties": {
            "datetime": "2026-07-31T12:00:00Z",
            "start_datetime": "2026-07-31T12:00:00Z",
            "end_datetime": "2026-07-31T12:00:21Z",
            "constellation": "Biomass",
            "sat:orbit_state": "descending",
            "sat:absolute_orbit": 3017,
            "sar:observation_direction": "left",
            "sar:instrument_mode": "SM",
            "eopf:datatake_id": "24719280",
            "eofeos:repeat_cycle_id": "1",
            "eofeos:major_cycle_id": "1",
            "processing:level": "L1B",
            "storage:schemes": {"example": "not copied"},
        },
        "links": [
            {
                "rel": "self",
                "href": "https://user:secret@example.test/items/test.json?token=secret#fragment",
            }
        ],
        "assets": {
            "enclosure_tiff": {
                "href": "https://user:secret@example.test/beta.tif?token=secret"
            },
            "enclosure_nc": {"href": "https://example.test/lut.nc?token=secret"},
            "enclosure_annot_xml": {
                "href": "https://example.test/annotation.xml?token=secret"
            },
        },
    }
    item.update(overrides)
    path.write_text(json.dumps(item), encoding="utf-8")

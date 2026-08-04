"""Tests for the standalone local PgSTAC loader."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def _loader_module():
    """Import the standalone loader without adding it to the package API."""
    specification = importlib.util.spec_from_file_location(
        "load_pgstac", ROOT / "scripts" / "load_pgstac.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_reads_only_items_registered_by_the_root_catalog(tmp_path: Path) -> None:
    """The loader uses Catalog Item links rather than an unsafe file scan."""
    catalog = tmp_path / "catalog.json"
    item = tmp_path / "32TNS" / "2026-01-02" / "source" / "item.json"
    ignored = tmp_path / ".temporary" / "item.json"
    item.parent.mkdir(parents=True)
    ignored.parent.mkdir()
    item.write_text("{}", encoding="utf-8")
    ignored.write_text("{}", encoding="utf-8")
    catalog.write_text(
        '{"links": [{"rel": "item", "href": "32TNS/2026-01-02/source/item.json"}]}',
        encoding="utf-8",
    )

    loader = _loader_module()

    assert loader.input_paths(tmp_path) == (catalog, [item])


def test_maps_local_asset_hrefs_to_the_container_mount(tmp_path: Path) -> None:
    """The database copy points TiTiler at the read-only container mount."""
    item = tmp_path / "32TNS" / "2026-01-02" / "source" / "item.json"
    item.parent.mkdir(parents=True)
    item.write_text(
        '{"assets": {"gamma0_hh": {"href": "gamma0_hh.tif"}, '
        '"source": {"href": "https://example.test/source.tif"}}}',
        encoding="utf-8",
    )
    loader = _loader_module()

    document = loader._read_document(item, tmp_path, "/data/gamma0")

    assert document["assets"]["gamma0_hh"]["href"] == (
        "file:///data/gamma0/32TNS/2026-01-02/source/gamma0_hh.tif"
    )
    assert document["assets"]["source"]["href"] == "https://example.test/source.tif"


def test_rejects_missing_root_catalog(tmp_path: Path) -> None:
    """The loader refuses an incomplete output root before opening PgSTAC."""
    loader = _loader_module()

    with pytest.raises(ValueError, match="catalog.json"):
        loader.input_paths(tmp_path)

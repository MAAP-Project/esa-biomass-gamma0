"""Tests for the local Item-ID staging and processing adapter."""

import json
from pathlib import Path

from esa_biomass_gamma0 import development
from pystac import Asset, Item


def _item() -> Item:
    """Create a source Item whose remote links contain ephemeral components."""
    item = Item(
        id="source/item",
        geometry=None,
        bbox=[10.0, 45.0, 11.0, 46.0],
        datetime=None,
        properties={
            "start_datetime": "2026-01-02T03:04:05Z",
            "end_datetime": "2026-01-02T03:04:05Z",
        },
    )
    item.collection_id = "BiomassLevel1b"
    item.set_self_href(
        "https://user:password@example.test/item.json?token=secret#fragment"
    )
    for key, suffix in (
        ("enclosure_tiff", "beta0.tif"),
        ("enclosure_nc", "radiometry.nc"),
        ("enclosure_annot_xml", "annotation.xml"),
    ):
        item.add_asset(
            key, Asset(href=f"https://user:password@example.test/{suffix}?token=secret")
        )
    return item


def test_stage_source_reuses_a_complete_cache_without_network(
    tmp_path: Path, monkeypatch
) -> None:
    """A complete local cache permits an offline staging run."""
    paths = development.cache_paths("source/item", tmp_path)
    for path in paths.values():
        development.write_cached_asset(path, b"cached")

    def fail_lookup(item_id: str) -> Item:
        raise AssertionError(f"unexpected lookup for {item_id}")

    monkeypatch.setattr(development, "find_source_item", fail_lookup)

    assert development.stage_source("source/item", tmp_path) == paths


def test_stage_source_refreshes_assets_and_sanitizes_cached_item(
    tmp_path: Path, monkeypatch
) -> None:
    """A refresh writes new local files without persisting source credentials."""
    source_item = _item()
    monkeypatch.setattr(development, "find_source_item", lambda item_id: source_item)
    monkeypatch.setattr(development, "request_access_token", lambda: "access-token")

    async def download_assets(urls: dict[str, str], token: str) -> dict[str, bytes]:
        assert token == "access-token"
        return {name: name.encode() for name in urls}

    monkeypatch.setattr(development, "fetch_assets", download_assets)

    paths = development.stage_source("source/item", tmp_path, refresh=True)

    assert {
        name: path.read_bytes() for name, path in paths.items() if name != "source_item"
    } == {
        "beta": b"beta",
        "lut": b"lut",
        "annotation": b"annotation",
    }
    cached_document = json.loads(paths["source_item"].read_text(encoding="utf-8"))
    assert "password" not in json.dumps(cached_document)
    assert "token=secret" not in json.dumps(cached_document)
    assert (
        cached_document["assets"]["enclosure_tiff"]["href"]
        == "https://example.test/beta0.tif"
    )


def test_local_runner_stages_then_delegates_to_the_staged_cli(
    tmp_path: Path, monkeypatch
) -> None:
    """The local command delegates processing through the staged CLI boundary."""
    paths = {
        "source_item": tmp_path / "source-item.json",
        "beta": tmp_path / "beta0.tif",
        "lut": tmp_path / "lut.nc",
        "annotation": tmp_path / "annotation.xml",
    }
    staged: list[object] = []

    def stage(item_id: str, cache_dir: Path, *, refresh: bool) -> dict[str, Path]:
        staged.extend((item_id, cache_dir, refresh))
        return paths

    monkeypatch.setattr(development, "stage_source", stage)
    captured: list[str] = []
    monkeypatch.setattr(
        development, "process_gamma0", lambda arguments: captured.extend(arguments) or 0
    )

    assert (
        development.stage_and_process_main(["source/item", "--refresh", "--overwrite"])
        == 0
    )

    assert staged == ["source/item", Path("/tmp/esa-biomass-gamma0").resolve(), True]
    assert captured == [
        "--source-item",
        str(paths["source_item"]),
        "--beta0-tiff",
        str(paths["beta"]),
        "--radiometry-lut",
        str(paths["lut"]),
        "--annotation-xml",
        str(paths["annotation"]),
        "--output-root",
        str(Path("output").resolve()),
        "--overwrite",
    ]

"""Tests for authenticated source materialization."""

import json
from pathlib import Path

import pytest

from esa_biomass_gamma0 import materialization
from pystac import Asset, Item


def _item() -> Item:
    """Create a source Item with signed remote asset URLs."""
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


def test_materialize_source_writes_sanitized_local_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One Item and its three assets materialize into a caller-owned directory."""
    monkeypatch.setattr(materialization, "find_source_item", lambda _: _item())
    monkeypatch.setattr(
        materialization, "request_access_token", lambda *_: "access-token"
    )

    async def download(urls: dict[str, str], token: str) -> dict[str, bytes]:
        assert token == "access-token"
        return {name: name.encode() for name in urls}

    monkeypatch.setattr(materialization, "fetch_assets", download)

    paths = materialization.materialize_source(
        "source/item", tmp_path / "staged", "client-secret", "offline-token"
    )

    assert {
        name: path.read_bytes() for name, path in paths.items() if name != "source_item"
    } == {
        "beta": b"beta",
        "lut": b"lut",
        "annotation": b"annotation",
    }
    document = json.loads(paths["source_item"].read_text(encoding="utf-8"))
    serialized = json.dumps(document)
    assert "password" not in serialized
    assert "token=secret" not in serialized


def test_materialize_source_removes_partial_files_after_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed download leaves no staged source artifacts behind."""
    destination = tmp_path / "staged"
    destination.mkdir()
    monkeypatch.setattr(materialization, "find_source_item", lambda _: _item())
    monkeypatch.setattr(
        materialization, "request_access_token", lambda *_: "access-token"
    )

    async def fail_download(_: dict[str, str], __: str) -> dict[str, bytes]:
        raise RuntimeError("https://example.test/beta0.tif?token=secret")

    monkeypatch.setattr(materialization, "fetch_assets", fail_download)

    with pytest.raises(RuntimeError, match="token=secret"):
        materialization.materialize_source(
            "source/item", destination, "client-secret", "offline-token"
        )

    assert list(destination.iterdir()) == []

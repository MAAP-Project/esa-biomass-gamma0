"""Tests for cached local Item-ID staging."""

from pathlib import Path

import pytest

from esa_biomass_gamma0 import materialization


def test_stage_source_reuses_a_complete_cache_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete local cache permits an offline staging run."""
    paths = materialization.cache_paths("source/item", tmp_path)
    for path in paths.values():
        materialization.write_asset(path, b"cached")

    monkeypatch.setattr(
        materialization,
        "materialize_source",
        lambda *_: pytest.fail("unexpected source materialization"),
    )

    assert materialization.stage_source("source/item", tmp_path) == paths


def test_stage_source_materializes_then_updates_the_local_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache miss delegates authenticated staging to the shared materializer."""
    captured: list[object] = []
    monkeypatch.setenv("ESA_MAAP_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("ESA_OFFLINE_TOKEN", "offline-token")

    def materialize(
        item_id: str, destination: Path, client_secret: str, offline_token: str
    ) -> dict[str, Path]:
        captured.extend((item_id, client_secret, offline_token))
        assert not list(destination.iterdir())
        paths = {
            "source_item": destination / "source-item.json",
            "beta": destination / "beta0.tif",
            "lut": destination / "radiometry.nc",
            "annotation": destination / "annotation.xml",
        }
        for name, path in paths.items():
            materialization.write_asset(path, name.encode())
        return paths

    monkeypatch.setattr(materialization, "materialize_source", materialize)

    paths = materialization.stage_source("source/item", tmp_path, refresh=True)

    assert captured == ["source/item", "client-secret", "offline-token"]
    assert {name: path.read_bytes() for name, path in paths.items()} == {
        "source_item": b"source_item",
        "beta": b"beta",
        "lut": b"lut",
        "annotation": b"annotation",
    }

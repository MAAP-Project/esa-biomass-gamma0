"""Tests for MAAP-secret Item-ID materialization."""

from pathlib import Path

import pytest

from esa_biomass_gamma0 import fetch


class _Secrets:
    """Small fake MAAP secret client."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.requested: list[str] = []

    def get_secret(self, name: str) -> str:
        """Return one configured secret value."""
        self.requested.append(name)
        return self.values[name]


class _MAAP:
    """Small fake MAAP client."""

    def __init__(self, values: dict[str, str]) -> None:
        self.secrets = _Secrets(values)


def test_materialize_item_reads_maap_secrets_then_materializes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Materialization receives credentials only from MAAP-managed secrets."""
    maap = _MAAP(
        {
            "ESA_MAAP_CLIENT_SECRET": "client-secret",
            "ESA_OFFLINE_TOKEN": "offline-token",
        }
    )
    monkeypatch.setattr(fetch, "maap_client", lambda: maap)
    captured: list[object] = []
    paths = {"source_item": tmp_path / "source-item.json"}

    def materialize(
        item_id: str, destination: Path, client_secret: str, offline_token: str
    ) -> dict[str, Path]:
        captured.extend((item_id, destination, client_secret, offline_token))
        return paths

    monkeypatch.setattr(fetch, "materialize_source", materialize)

    assert fetch.materialize_item("source/item", tmp_path) == paths
    assert maap.secrets.requested == ["ESA_MAAP_CLIENT_SECRET", "ESA_OFFLINE_TOKEN"]
    assert captured == ["source/item", tmp_path, "client-secret", "offline-token"]


def test_materialize_item_rejects_missing_secrets_before_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing MAAP secrets fail before source materialization."""
    maap = _MAAP({"ESA_MAAP_CLIENT_SECRET": "client-secret", "ESA_OFFLINE_TOKEN": ""})
    monkeypatch.setattr(fetch, "maap_client", lambda: maap)
    monkeypatch.setattr(
        fetch, "materialize_source", lambda *_: pytest.fail("must not materialize")
    )

    with pytest.raises(ValueError, match="Missing MAAP fetch secret"):
        fetch.materialize_item("source/item", tmp_path)

"""MAAP-secret Item-ID materialization for the Gamma0 fetch command."""

from pathlib import Path

from esa_biomass_gamma0.materialization import materialize_source


def materialize_item(item_id: str, destination: Path) -> dict[str, Path]:
    """Materialize one source Item with MAAP-managed secrets."""
    secrets = maap_client().secrets
    client_secret = secrets.get_secret("ESA_MAAP_CLIENT_SECRET")
    offline_token = secrets.get_secret("ESA_OFFLINE_TOKEN")
    if not client_secret or not offline_token:
        raise ValueError("Missing MAAP fetch secret")
    return materialize_source(item_id, destination, client_secret, offline_token)


def maap_client() -> object:
    """Create the MAAP client only in the network-enabled fetch execution path."""
    from maap.maap import MAAP

    return MAAP()

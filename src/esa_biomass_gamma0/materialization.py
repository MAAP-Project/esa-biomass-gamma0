"""Authenticated source materialization for fetch and local development adapters."""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.parse import urlsplit

from esa_biomass_gamma0.source import sanitize_href
from pystac import Item

ESA_STAC_API_URL = "https://catalog.maap.eo.esa.int/catalogue/"
TOKEN_URL = "https://iam.maap.eo.esa.int/realms/esa-maap/protocol/openid-connect/token"
REQUIRED_ASSETS = {
    "beta": "enclosure_tiff",
    "lut": "enclosure_nc",
    "annotation": "enclosure_annot_xml",
}
logger = logging.getLogger(__name__)


def materialize_source(
    item_id: str, destination: Path, client_secret: str, offline_token: str
) -> dict[str, Path]:
    """Materialize an Item and required assets into an initially empty directory."""
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("source materialization destination must be empty")

    try:
        item = find_source_item(item_id)
        asset_urls = _required_asset_urls(item)
        contents = asyncio.run(
            fetch_assets(asset_urls, request_access_token(client_secret, offline_token))
        )
        paths = {
            "source_item": destination / "source-item.json",
            "beta": destination / "beta0.tif",
            "lut": destination / "radiometry.nc",
            "annotation": destination / "annotation.xml",
        }
        for name, content in contents.items():
            write_asset(paths[name], content)
        write_asset(
            paths["source_item"],
            json.dumps(_sanitized_document(item.to_dict()), indent=2).encode(),
        )
        return paths
    except Exception:
        _clear_directory(destination)
        raise


def cache_paths(item_id: str, cache_dir: Path) -> dict[str, Path]:
    """Return stable local paths for an Item's staged source files."""
    safe_item_id = item_id.replace("/", "_")
    prefix = cache_dir / f"biomass__{safe_item_id}"
    return {
        "source_item": prefix.with_name(f"{prefix.name}__item.json"),
        "beta": prefix.with_name(f"{prefix.name}__beta0.tif"),
        "lut": prefix.with_name(f"{prefix.name}__lut.nc"),
        "annotation": prefix.with_name(f"{prefix.name}__annotation.xml"),
    }


def stage_source(
    item_id: str, cache_dir: Path, *, refresh: bool = False
) -> dict[str, Path]:
    """Stage one Item locally, reusing a complete cache unless refreshed."""
    paths = cache_paths(item_id, cache_dir)
    if not refresh and all(path.is_file() for path in paths.values()):
        logger.info("Using cached source files for %s", item_id)
        return paths

    client_secret = os.getenv("ESA_MAAP_CLIENT_SECRET")
    offline_token = os.getenv("ESA_OFFLINE_TOKEN")
    if not client_secret or not offline_token:
        raise ValueError("Missing local MAAP credentials")

    cache_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=cache_dir) as temporary:
        materialized = materialize_source(
            item_id, Path(temporary), client_secret, offline_token
        )
        for name, path in paths.items():
            materialized[name].replace(path)
    return paths


def find_source_item(item_id: str) -> Item:
    """Find one BIOMASS L1B Item through the MAAP STAC API."""
    from pystac_client import Client

    logger.info("Searching STAC for item %s", item_id)
    item = next(
        Client.open(ESA_STAC_API_URL)
        .search(ids=[item_id], collections=["BiomassLevel1b"], limit=1)
        .items(),
        None,
    )
    if item is None:
        raise ValueError(f"No BIOMASS L1B item found with id {item_id!r}")
    return item


def request_access_token(client_secret: str, offline_token: str) -> str:
    """Exchange supplied MAAP credentials for an access token."""
    import requests

    if not client_secret or not offline_token:
        raise ValueError("Missing MAAP credentials")
    logger.info("Requesting a MAAP access token")
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": "offline-token",
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": offline_token,
            "scope": "offline_access openid",
        },
        timeout=60,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Failed to retrieve an access token from the IAM response")
    return token


async def fetch_assets(urls: dict[str, str], token: str) -> dict[str, bytes]:
    """Download authenticated source assets without logging their URLs."""
    import obstore as obs
    from obstore.store import HTTPStore

    downloads = {}
    for name, url in urls.items():
        parsed_url = urlsplit(url)
        store = HTTPStore(
            f"{parsed_url.scheme}://{parsed_url.netloc}",
            client_options={
                "default_headers": {"Authorization": f"Bearer {token}"},
                "timeout": "3m",
            },
        )
        downloads[name] = obs.get_async(store, parsed_url.path.lstrip("/"))

    logger.info("Downloading %d source asset(s)", len(downloads))
    responses = await asyncio.gather(*downloads.values())
    contents = await asyncio.gather(*(response.bytes_async() for response in responses))
    return dict(zip(downloads, map(bytes, contents), strict=True))


def write_asset(path: Path, contents: bytes) -> None:
    """Atomically write one local staged file."""
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(contents)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _required_asset_urls(item: Item) -> dict[str, str]:
    try:
        asset_urls = {
            name: item.assets[key].href for name, key in REQUIRED_ASSETS.items()
        }
    except KeyError as error:
        raise ValueError(
            f"{item.id} is missing required asset {error.args[0]}"
        ) from error
    if not all(asset_urls.values()):
        raise ValueError(f"{item.id} has an empty required asset URL")
    return asset_urls


def _clear_directory(directory: Path) -> None:
    for path in directory.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _sanitized_document(value: object) -> object:
    """Return a STAC document value with every href safe for local storage."""
    if isinstance(value, dict):
        return {
            key: sanitize_href(item)
            if key == "href" and isinstance(item, str)
            else _sanitized_document(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitized_document(item) for item in value]
    return value

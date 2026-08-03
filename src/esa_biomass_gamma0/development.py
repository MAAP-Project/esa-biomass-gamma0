"""Local-only MAAP staging adapter for development runs."""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Sequence
from urllib.parse import urlsplit

from esa_biomass_gamma0.cli import main as process_gamma0
from esa_biomass_gamma0.source import sanitize_href
from pystac import Item

ESA_STAC_API_URL = "https://catalog.maap.eo.esa.int/catalogue/"
logger = logging.getLogger(__name__)


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


def write_cached_asset(path: Path, contents: bytes) -> None:
    """Atomically write one local staged file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(contents)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


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


def request_access_token() -> str:
    """Exchange local MAAP credentials for an access token."""
    import requests

    client_secret = os.getenv("ESA_MAAP_CLIENT_SECRET")
    offline_token = os.getenv("ESA_OFFLINE_TOKEN")
    if not all((client_secret, offline_token)):
        raise ValueError("Missing ESA_MAAP_CLIENT_SECRET or ESA_OFFLINE_TOKEN env var")
    logger.info("Requesting a MAAP access token")
    response = requests.post(
        "https://iam.maap.eo.esa.int/realms/esa-maap/protocol/openid-connect/token",
        data={
            "client_id": os.getenv("MAAP_CLIENT_ID", "offline-token"),
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


def stage_source(
    item_id: str, cache_dir: Path, *, refresh: bool = False
) -> dict[str, Path]:
    """Stage one Item locally, reusing a complete cache unless refreshed."""
    paths = cache_paths(item_id, cache_dir)
    if not refresh and all(path.is_file() for path in paths.values()):
        logger.info("Using cached source files for %s", item_id)
        return paths

    item = find_source_item(item_id)
    try:
        asset_urls = {
            "beta": item.assets["enclosure_tiff"].href,
            "lut": item.assets["enclosure_nc"].href,
            "annotation": item.assets["enclosure_annot_xml"].href,
        }
    except KeyError as error:
        raise ValueError(
            f"{item.id} is missing required asset {error.args[0]}"
        ) from error

    if not all(asset_urls.values()):
        raise ValueError(f"{item.id} has an empty required asset URL")

    required_assets = (
        asset_urls
        if refresh
        else {
            name: url for name, url in asset_urls.items() if not paths[name].is_file()
        }
    )
    if required_assets:
        downloaded = asyncio.run(fetch_assets(required_assets, request_access_token()))
        for name, contents in downloaded.items():
            write_cached_asset(paths[name], contents)
    write_cached_asset(
        paths["source_item"],
        json.dumps(_sanitized_document(item.to_dict()), indent=2).encode(),
    )
    return paths


def stage_and_process_main(argv: Sequence[str] | None = None) -> int:
    """Stage one Item by ID and run the production staged-source CLI."""
    parser = argparse.ArgumentParser(
        description="Stage one BIOMASS L1B Item locally and create Gamma0 products."
    )
    parser.add_argument("item_id", help="BIOMASS L1B STAC Item ID")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp/esa-biomass-gamma0"),
        help="Persistent staged-source cache (default: /tmp/esa-biomass-gamma0).",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("output"),
        help="Tile product and STAC output root (default: output).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh the cached Item and all three source assets.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Rebuild complete existing tile products.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Logging threshold (default: INFO).",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, arguments.log_level), format="%(levelname)s %(message)s"
    )
    try:
        paths = stage_source(
            arguments.item_id,
            arguments.cache_dir.expanduser().resolve(),
            refresh=arguments.refresh,
        )
    except Exception:
        logger.error("Local source staging failed")
        return 1

    command = [
        "--source-item",
        str(paths["source_item"]),
        "--beta0-tiff",
        str(paths["beta"]),
        "--radiometry-lut",
        str(paths["lut"]),
        "--annotation-xml",
        str(paths["annotation"]),
        "--output-root",
        str(arguments.output_root.expanduser().resolve()),
    ]
    if arguments.overwrite:
        command.append("--overwrite")
    return process_gamma0(command)


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

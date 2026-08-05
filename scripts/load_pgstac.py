#!/usr/bin/env python3
"""Load one local Gamma0 STAC output root into PgSTAC."""

import argparse
import json
import logging
import tempfile
from pathlib import Path, PurePosixPath
from typing import Sequence
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def input_paths(output_root: Path) -> tuple[Path, list[Path]]:
    """Return the root Catalog and its registered local Item paths."""
    output_root = Path(output_root).resolve()
    catalog = output_root / "catalog.json"
    if not catalog.is_file():
        raise ValueError(f"missing root Catalog: {catalog}")

    try:
        document = json.loads(catalog.read_text(encoding="utf-8"))
        links = document["links"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"invalid root Catalog: {catalog}") from error
    if not isinstance(links, list):
        raise ValueError(f"invalid root Catalog links: {catalog}")

    items: list[Path] = []
    for link in links:
        if not isinstance(link, dict) or link.get("rel") != "item":
            continue
        href = link.get("href")
        if not isinstance(href, str):
            raise ValueError(f"invalid Item link in root Catalog: {catalog}")
        item = (catalog.parent / href).resolve()
        if output_root not in item.parents or not item.is_file():
            raise ValueError(f"invalid Item link in root Catalog: {href}")
        items.append(item)
    return catalog, items


def load(
    output_root: Path, asset_root: PurePosixPath = PurePosixPath("/data/gamma0")
) -> int:
    """Upsert Catalog Items under an ephemeral Collection for PgSTAC."""
    from pystac import Item
    from pypgstac.db import PgstacDB
    from pypgstac.load import Loader, Methods

    from esa_biomass_gamma0.stac import create_collection

    output_root = Path(output_root).resolve()
    _, items = input_paths(output_root)
    collection = create_collection([Item.from_file(str(path)) for path in items])
    documents = [_read_document(path, output_root, asset_root) for path in items]
    for document in documents:
        document["collection"] = collection.id
    with tempfile.TemporaryDirectory() as directory:
        collection_path = Path(directory) / "collection.json"
        collection.set_self_href(str(collection_path))
        collection.validate()
        collection_path.write_text(json.dumps(collection.to_dict()), encoding="utf-8")
        with PgstacDB() as database:
            loader = Loader(database)
            loader.load_collections(str(collection_path), insert_mode=Methods.upsert)
            if documents:
                loader.load_items(iter(documents), insert_mode=Methods.upsert)
    logger.info("Loaded %d Item(s) into PgSTAC", len(documents))
    return len(documents)


def main(argv: Sequence[str] | None = None) -> int:
    """Load a local output root and return a process exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_root",
        type=Path,
        nargs="?",
        default=Path("output"),
        help="Gamma0 output root containing catalog.json (default: output).",
    )
    parser.add_argument(
        "--asset-root",
        type=PurePosixPath,
        default=PurePosixPath("/data/gamma0"),
        help="Container path where this output root is mounted (default: /data/gamma0).",
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
        load(arguments.output_root, arguments.asset_root)
    except Exception:
        logger.error("PgSTAC load failed")
        return 1
    return 0


def _read_document(path: Path, output_root: Path, asset_root: PurePosixPath) -> dict:
    """Read one Item and map its relative assets to a container file URI."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid Item: {path}") from error
    if not isinstance(document, dict):
        raise ValueError(f"invalid Item: {path}")
    assets = document.get("assets", {})
    if not isinstance(assets, dict):
        raise ValueError(f"invalid Item assets: {path}")
    relative_directory = path.parent.relative_to(output_root)
    for asset in assets.values():
        if not isinstance(asset, dict) or not isinstance(asset.get("href"), str):
            raise ValueError(f"invalid Item asset: {path}")
        asset["href"] = _container_href(asset["href"], relative_directory, asset_root)
    return document


def _container_href(
    href: str, relative_directory: Path, asset_root: PurePosixPath
) -> str:
    """Map one relative asset href to its file URI under the TiTiler mount."""
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return href
    asset_path = PurePosixPath(parsed.path)
    if (
        asset_path.is_absolute()
        or ".." in asset_path.parts
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"invalid local asset href: {href}")
    container_path = (
        asset_root / PurePosixPath(relative_directory.as_posix()) / asset_path
    )
    return f"file://{container_path}"


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate portable sample STAC metadata for this product."""

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from pystac import CatalogType

from esa_biomass_gamma0 import __version__
from esa_biomass_gamma0.grids import target_grid
from esa_biomass_gamma0.source import StagedSource
from esa_biomass_gamma0.stac import (
    ITEM_ASSETS,
    THUMBNAIL_KEY,
    build_item,
    create_collection,
)

logger = logging.getLogger(__name__)
DEFAULT_OUTPUT_DIRECTORY = Path(__file__).parents[1] / "examples" / "stac"


def generate(output_directory: Path) -> tuple[Path, Path]:
    """Write a Collection and representative Item without raster payloads."""
    output_directory.mkdir(parents=True, exist_ok=True)
    grid = target_grid("32TPR")
    with TemporaryDirectory() as temporary_directory:
        assets_directory = Path(temporary_directory)
        for key in ITEM_ASSETS:
            filename = "thumbnail.png" if key == THUMBNAIL_KEY else f"{key}.tif"
            (assets_directory / filename).touch()
        item = build_item(
            _source(assets_directory),
            grid,
            assets_directory,
            processing_version=__version__,
        )
        collection = create_collection([item])
        collection.normalize_hrefs(str(output_directory))
        collection.save(catalog_type=CatalogType.SELF_CONTAINED)

    return (
        output_directory / "collection.json",
        output_directory / item.id / f"{item.id}.json",
    )


def _source(directory: Path) -> StagedSource:
    """Return stable, safe source metadata for the checked-in example."""
    return StagedSource(
        item_id="BIOMASS_EXAMPLE_001",
        collection_id="BiomassLevel1b",
        datetime=datetime(2026, 7, 31, 12, tzinfo=timezone.utc),
        bbox=(10.0, 45.0, 11.0, 46.0),
        self_href="https://example.test/items/BIOMASS_EXAMPLE_001.json",
        asset_hrefs={
            "enclosure_tiff": "https://example.test/assets/beta0.tif",
            "enclosure_nc": "https://example.test/assets/gamma-nought.nc",
            "enclosure_annot_xml": "https://example.test/assets/annotation.xml",
        },
        source_item=directory / "source-item.json",
        beta0_tiff=directory / "beta0.tif",
        radiometry_lut=directory / "gamma-nought.nc",
        annotation_xml=directory / "annotation.xml",
    )


def main() -> None:
    """Generate the checked-in STAC metadata example."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    arguments = parser.parse_args()
    collection_path, item_path = generate(arguments.output_dir)
    logger.info("wrote %s and %s", collection_path, item_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()

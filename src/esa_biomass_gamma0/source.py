"""Validation for the local staged-source trust boundary."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

from pystac import Item, STACError

REQUIRED_ASSET_KEYS = (
    "enclosure_tiff",
    "enclosure_nc",
    "enclosure_annot_xml",
)


@dataclass(frozen=True)
class StagedSource:
    """Validated local inputs and sanitized source provenance for one granule."""

    item_id: str
    collection_id: str
    datetime: datetime
    bbox: tuple[float, float, float, float]
    self_href: str | None
    asset_hrefs: Mapping[str, str]
    source_item: Path
    beta0_tiff: Path
    radiometry_lut: Path
    annotation_xml: Path
    study_tiles: Path


def sanitize_href(href: str) -> str:
    """Remove credentials and ephemeral URL components from source provenance."""
    parsed = urlsplit(href)
    host = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def validate_staged_source(
    source_item: Path,
    beta0_tiff: Path,
    radiometry_lut: Path,
    annotation_xml: Path,
    study_tiles: Path,
) -> StagedSource:
    """Validate one staged source Item and its five local regular-file inputs."""
    paths = {
        "source_item": Path(source_item),
        "beta0_tiff": Path(beta0_tiff),
        "radiometry_lut": Path(radiometry_lut),
        "annotation_xml": Path(annotation_xml),
        "study_tiles": Path(study_tiles),
    }
    for name, path in paths.items():
        _require_readable_file(name, path)

    try:
        document = json.loads(paths["source_item"].read_text(encoding="utf-8"))
        item = Item.from_dict(document)
    except (json.JSONDecodeError, STACError, TypeError, ValueError) as error:
        raise ValueError(f"source_item: invalid STAC Item: {error}") from error

    if not item.id:
        raise ValueError("source_item: missing Item id")
    if not item.collection_id:
        raise ValueError("source_item: missing collection")
    if item.datetime is None or item.datetime.tzinfo is None:
        raise ValueError("source_item: missing timezone-aware datetime")

    bbox = _validate_bbox(item.bbox)
    asset_hrefs: dict[str, str] = {}
    for key in REQUIRED_ASSET_KEYS:
        asset = item.assets.get(key)
        if asset is None or not asset.href:
            raise ValueError(f"source_item: missing required asset {key}")
        asset_hrefs[key] = sanitize_href(asset.href)

    self_href = item.get_self_href()
    return StagedSource(
        item_id=item.id,
        collection_id=item.collection_id,
        datetime=item.datetime.astimezone(timezone.utc),
        bbox=bbox,
        self_href=sanitize_href(self_href) if self_href else None,
        asset_hrefs=MappingProxyType(asset_hrefs),
        **paths,
    )


def _require_readable_file(name: str, path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"{name}: expected a readable regular file at {path}")
    try:
        with path.open("rb") as file:
            file.read(1)
    except OSError as error:
        raise ValueError(f"{name}: cannot read {path}") from error


def _validate_bbox(bbox: list[float] | None) -> tuple[float, float, float, float]:
    if bbox is None or len(bbox) not in (4, 6):
        raise ValueError("source_item: bbox must contain four or six coordinates")
    if len(bbox) == 4:
        west, south, east, north = bbox
    else:
        west, south, _, east, north, _ = bbox
    values = (float(west), float(south), float(east), float(north))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("source_item: bbox contains a non-finite coordinate")
    west, south, east, north = values
    if west > east:
        raise ValueError("source_item: antimeridian bbox is unsupported")
    if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
        raise ValueError("source_item: malformed bbox")
    if south <= -80 or north >= 84:
        raise ValueError("source_item: polar bbox is unsupported for UTM output")
    return values

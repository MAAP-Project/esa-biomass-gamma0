"""Command-line adapter for the staged Gamma0 workflow."""

import argparse
import logging
from pathlib import Path
from typing import Sequence

from esa_biomass_gamma0.grids import RESOLUTION_METERS
from esa_biomass_gamma0.workflow import WorkflowResult, process_source

DEFAULT_WINDOW_PADDING_PIXELS = 64

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse local staged-source arguments for one Gamma0 workflow run."""
    parser = argparse.ArgumentParser(
        description="Create fixed-grid Gamma0 products from staged local files."
    )
    for flag, destination, help_text in (
        ("--source-item", "source_item", "Staged source STAC Item JSON."),
        ("--beta0-tiff", "beta0_tiff", "Staged four-band Beta0 TIFF."),
        ("--radiometry-lut", "radiometry_lut", "Staged radiometry LUT NetCDF."),
        ("--annotation-xml", "annotation_xml", "Staged annotation XML."),
    ):
        parser.add_argument(
            flag,
            dest=destination,
            required=True,
            type=_normalized_path,
            help=help_text,
        )
    parser.add_argument(
        "--output-root",
        type=_normalized_path,
        default=_normalized_path("output"),
        help="Directory for tile products and local STAC metadata (default: output).",
    )
    parser.add_argument(
        "--resolution",
        type=float,
        default=RESOLUTION_METERS,
        help=f"Fixed output resolution in metres (must be {RESOLUTION_METERS:g}).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Rebuild complete existing tile products.",
    )
    parser.add_argument(
        "--window-padding-pixels",
        type=int,
        default=DEFAULT_WINDOW_PADDING_PIXELS,
        help="Radar-window padding for scientific tuning (default: 64).",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Logging threshold (default: INFO).",
    )
    arguments = parser.parse_args(argv)
    if arguments.resolution != RESOLUTION_METERS:
        parser.error(f"resolution must be {RESOLUTION_METERS:g} m")
    if arguments.window_padding_pixels < 0:
        parser.error("window padding must be non-negative")
    _require_staged_files(parser, arguments)
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    """Run the staged-source workflow and return its process exit status."""
    arguments = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, arguments.log_level), format="%(levelname)s %(message)s"
    )
    try:
        result = process_source(
            source_item=arguments.source_item,
            beta0_tiff=arguments.beta0_tiff,
            radiometry_lut=arguments.radiometry_lut,
            annotation_xml=arguments.annotation_xml,
            output_root=arguments.output_root,
            resolution=arguments.resolution,
            overwrite=arguments.overwrite,
            window_padding_pixels=arguments.window_padding_pixels,
        )
    except Exception:
        logger.error("Gamma0 processing failed")
        return 1
    _log_result(result)
    return int(result.failed > 0)


def _normalized_path(value: str) -> Path:
    """Return an absolute local path without opening it."""
    return Path(value).expanduser().resolve()


def _require_staged_files(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> None:
    """Reject missing staged files before entering the scientific workflow."""
    for flag, path in (
        ("--source-item", arguments.source_item),
        ("--beta0-tiff", arguments.beta0_tiff),
        ("--radiometry-lut", arguments.radiometry_lut),
        ("--annotation-xml", arguments.annotation_xml),
    ):
        if not path.is_file():
            parser.error(f"{flag} must name a local file")


def _log_result(result: WorkflowResult) -> None:
    """Log concise workflow counts without serializing source metadata."""
    logger.info(
        "Gamma0 processing complete: candidates=%d written=%d skipped_complete=%d "
        "skipped_no_data=%d failed=%d",
        result.candidates,
        result.written,
        result.skipped_complete,
        result.skipped_no_data,
        result.failed,
    )

"""Typer commands for staged and Item-ID Gamma0 processing."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

import typer

from esa_biomass_gamma0.materialization import stage_source
from esa_biomass_gamma0.workflow import process_source

DEFAULT_WINDOW_PADDING_PIXELS = 64
logger = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True)

LocalFile = Annotated[
    Path,
    typer.Option(
        callback=lambda value: _local_file(value),
        help="Staged local file.",
    ),
]
OutputRoot = Annotated[
    Path,
    typer.Option(
        callback=lambda value: _normalized_path(value), help="Output directory."
    ),
]
CacheDirectory = Annotated[
    Path,
    typer.Option(
        callback=lambda value: _normalized_path(value),
        help="Persistent staged-source cache.",
    ),
]
LogLevel = Annotated[
    Literal["DEBUG", "INFO", "WARNING", "ERROR"],
    typer.Option(help="Logging threshold (default: INFO)."),
]


@app.command()
def staged(
    source_item: LocalFile,
    beta0_tiff: LocalFile,
    radiometry_lut: LocalFile,
    annotation_xml: LocalFile,
    output_root: OutputRoot = Path("output"),
    window_padding_pixels: Annotated[
        int,
        typer.Option(min=0, help="Radar-window padding for scientific tuning."),
    ] = DEFAULT_WINDOW_PADDING_PIXELS,
    log_level: LogLevel = "INFO",
) -> None:
    """Create fixed-grid Gamma0 products from staged local files."""
    _configure_logging(log_level)
    _exit_if_failed(
        process_staged(
            source_item=source_item,
            beta0_tiff=beta0_tiff,
            radiometry_lut=radiometry_lut,
            annotation_xml=annotation_xml,
            output_root=output_root,
            window_padding_pixels=window_padding_pixels,
        )
    )


@app.command()
def local(
    item_id: Annotated[str, typer.Argument(help="BIOMASS L1B STAC Item ID")],
    cache_dir: CacheDirectory = Path("/tmp/esa-biomass-gamma0"),
    output_root: OutputRoot = Path("output"),
    refresh: Annotated[
        bool, typer.Option(help="Refresh the cached source files.")
    ] = False,
    log_level: LogLevel = "INFO",
) -> None:
    """Materialize one Item with local credentials and create Gamma0 products."""
    _configure_logging(log_level)
    try:
        paths = stage_source(item_id, cache_dir, refresh=refresh)
    except Exception:
        logger.error("Local source staging failed")
        raise typer.Exit(1) from None
    _exit_if_failed(
        process_staged(
            source_item=paths["source_item"],
            beta0_tiff=paths["beta"],
            radiometry_lut=paths["lut"],
            annotation_xml=paths["annotation"],
            output_root=output_root,
        )
    )


@app.command()
def fetch(
    item_id: Annotated[str, typer.Argument(help="BIOMASS L1B STAC Item ID")],
    output_root: OutputRoot = Path("output"),
    log_level: LogLevel = "INFO",
) -> None:
    """Fetch one BIOMASS L1B Item and create Gamma0 products."""
    _configure_logging(log_level)
    try:
        from esa_biomass_gamma0.fetch import materialize_item

        with TemporaryDirectory(prefix="esa-biomass-gamma0-") as temporary:
            paths = materialize_item(item_id, Path(temporary))
            status = process_staged(
                source_item=paths["source_item"],
                beta0_tiff=paths["beta"],
                radiometry_lut=paths["lut"],
                annotation_xml=paths["annotation"],
                output_root=output_root,
                window_padding_pixels=DEFAULT_WINDOW_PADDING_PIXELS,
            )
    except Exception as error:
        logger.error(
            "Source fetch failed (%s)",
            type(error).__name__,
            exc_info=(type(error), None, error.__traceback__),
        )
        raise typer.Exit(1) from None
    _exit_if_failed(status)


def main() -> None:
    """Run the Gamma0 command-line application."""
    app()


def process_staged(
    *,
    source_item: Path,
    beta0_tiff: Path,
    radiometry_lut: Path,
    annotation_xml: Path,
    output_root: Path,
    window_padding_pixels: int = DEFAULT_WINDOW_PADDING_PIXELS,
) -> int:
    """Run the staged workflow and return its process exit status."""
    try:
        result = process_source(
            source_item=source_item,
            beta0_tiff=beta0_tiff,
            radiometry_lut=radiometry_lut,
            annotation_xml=annotation_xml,
            output_root=output_root,
            window_padding_pixels=window_padding_pixels,
        )
    except Exception:
        logger.error("Gamma0 processing failed")
        return 1

    logger.info(
        "Gamma0 processing complete: candidates=%d written=%d skipped_no_data=%d failed=%d",
        result.candidates,
        result.written,
        result.skipped_no_data,
        result.failed,
    )
    return int(result.failed > 0)


def _configure_logging(log_level: LogLevel) -> None:
    """Configure concise CLI logging."""
    logging.basicConfig(
        level=getattr(logging, log_level), format="%(levelname)s %(message)s"
    )


def _exit_if_failed(status: int) -> None:
    """Exit a Typer command when its workflow returned a failure status."""
    if status:
        raise typer.Exit(status)


def _local_file(value: Path) -> Path:
    """Normalize a local regular file without echoing unsafe input values."""
    path = _normalized_path(value)
    if not path.is_file():
        raise typer.BadParameter("must name a local file")
    return path


def _normalized_path(value: Path | str) -> Path:
    """Return an absolute local path without opening it."""
    return Path(value).expanduser().resolve()

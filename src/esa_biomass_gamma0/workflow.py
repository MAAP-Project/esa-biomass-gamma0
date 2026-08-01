"""Sequential staged-source orchestration for Gamma0 MGRS products."""

from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import tempfile

import numpy as np
from rasterio import open as open_raster
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.io import DatasetReader
from rasterio.windows import Window

from esa_biomass_gamma0 import __version__
from esa_biomass_gamma0.calibration import (
    CalibrationMetadata,
    LutCoordinates,
    calculate_gamma0,
    parse_annotation,
    read_lut_coordinates,
    sample_gamma_nought,
    window_coordinates,
)
from esa_biomass_gamma0.grids import (
    RESOLUTION_METERS,
    TileGrid,
    candidate_grids,
    gcp_pixel_window,
    shifted_gcps,
)
from esa_biomass_gamma0.raster import (
    warp_scientific_arrays,
    write_scientific_cogs,
    write_thumbnail,
)
from esa_biomass_gamma0.source import StagedSource, validate_staged_source
from esa_biomass_gamma0.stac import (
    build_item,
    is_complete_product,
    rebuild_catalog,
    source_footprint,
    write_item,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkflowResult:
    """Concise counts from processing one staged source granule."""

    candidates: int
    written: int
    skipped_complete: int
    skipped_no_data: int
    failed: int


def process_source(
    source_item: Path,
    beta0_tiff: Path,
    radiometry_lut: Path,
    annotation_xml: Path,
    output_root: Path,
    *,
    resolution: float = RESOLUTION_METERS,
    overwrite: bool = False,
    window_padding_pixels: int = 64,
    processing_version: str = __version__,
) -> WorkflowResult:
    """Process one local staged source into validated MGRS tile products and STAC."""
    if resolution != RESOLUTION_METERS:
        raise ValueError(f"resolution must be {RESOLUTION_METERS:g} m")
    if window_padding_pixels < 0:
        raise ValueError("window padding must be non-negative")
    source = validate_staged_source(
        source_item, beta0_tiff, radiometry_lut, annotation_xml
    )
    metadata = parse_annotation(source.annotation_xml)
    coordinates = read_lut_coordinates(source.radiometry_lut)
    output_root = Path(output_root)

    written = skipped_complete = skipped_no_data = failed = 0
    with open_raster(source.beta0_tiff) as dataset:
        if dataset.count != 4:
            raise ValueError("Beta0 must contain exactly four polarizations")
        gcps, gcp_crs = dataset.gcps
        if not gcps or gcp_crs is None:
            raise ValueError("Beta0 is missing GCPs or a GCP CRS")
        grids = candidate_grids(source.bbox)
        for grid in grids:
            directory = (
                output_root
                / grid.tile_id
                / source.datetime.date().isoformat()
                / source.item_id
            )
            if not overwrite and is_complete_product(directory):
                skipped_complete += 1
                continue
            window = gcp_pixel_window(
                grid,
                gcps,
                gcp_crs,
                dataset.height,
                dataset.width,
                padding_pixels=window_padding_pixels,
            )
            if window is None:
                skipped_no_data += 1
                continue
            try:
                product = _write_product(
                    directory,
                    source=source,
                    grid=grid,
                    dataset=dataset,
                    window=window,
                    gcps=gcps,
                    gcp_crs=gcp_crs,
                    metadata=metadata,
                    coordinates=coordinates,
                    processing_version=processing_version,
                )
            except Exception:
                failed += 1
                logger.exception("failed Gamma0 tile product %s", grid.tile_id)
                continue
            if product:
                written += 1
            else:
                skipped_no_data += 1
    rebuild_catalog(output_root)
    return WorkflowResult(
        candidates=len(grids),
        written=written,
        skipped_complete=skipped_complete,
        skipped_no_data=skipped_no_data,
        failed=failed,
    )


def _write_product(
    directory: Path,
    *,
    source: StagedSource,
    grid: TileGrid,
    dataset: DatasetReader,
    window: Window,
    gcps: list[GroundControlPoint],
    gcp_crs: CRS,
    metadata: CalibrationMetadata,
    coordinates: LutCoordinates,
    processing_version: str,
) -> bool:
    """Stage, validate, and atomically promote one accepted tile product."""
    beta0 = _read_beta0(dataset, window)
    azimuth, slant_range = window_coordinates(metadata, window)
    gamma_nought = sample_gamma_nought(
        source.radiometry_lut, coordinates, azimuth, slant_range
    )
    gamma0 = calculate_gamma0(beta0, gamma_nought)
    warped = warp_scientific_arrays(
        beta0,
        gamma0,
        gamma_nought,
        shifted_gcps(gcps, window),
        gcp_crs,
        grid,
    )
    if warped is None:
        return False
    warped_beta0, warped_gamma0, warped_gamma_nought = warped
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{source.item_id}-{grid.tile_id}-", dir=directory.parent
        )
    )
    write_scientific_cogs(
        temporary,
        grid,
        warped_beta0,
        warped_gamma0,
        warped_gamma_nought,
        source_item_id=source.item_id,
        processing_version=processing_version,
    )
    write_thumbnail(temporary / "thumbnail.png", warped_gamma0)
    item = build_item(
        source,
        grid,
        temporary,
        processing_version=processing_version,
        geometry=source_footprint(grid, gcps, gcp_crs, dataset.height, dataset.width),
    )
    write_item(item, temporary / "item.json")
    _promote_product(temporary, directory)
    return True


def _read_beta0(dataset: DatasetReader, window: Window) -> np.ndarray:
    """Read one local four-band Beta0 window and normalize source nodata to NaN."""
    beta0 = np.asarray(
        dataset.read(window=window, out_dtype="float32"), dtype="float32"
    )
    masks = dataset.read_masks(window=window)
    beta0[masks == 0] = np.nan
    if dataset.nodata is not None:
        beta0[beta0 == dataset.nodata] = np.nan
    return beta0


def _promote_product(temporary: Path, directory: Path) -> None:
    """Replace a product leaf only after its complete replacement is ready."""
    if not directory.exists():
        temporary.replace(directory)
        return
    backup = Path(
        tempfile.mkdtemp(prefix=f".{directory.name}-previous-", dir=directory.parent)
    )
    backup.rmdir()
    directory.replace(backup)
    try:
        temporary.replace(directory)
    except Exception:
        backup.replace(directory)
        raise
    shutil.rmtree(backup)

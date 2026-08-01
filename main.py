"""Write BIOMASS L1B Gamma0 alignment diagnostics as Cloud Optimized GeoTIFFs."""

import argparse
import asyncio
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

import numpy as np
import obstore as obs
import rasterio
import requests
from affine import Affine
from netCDF4 import Dataset
from obstore.store import HTTPStore
from pystac_client import Client
from rasterio.control import GroundControlPoint
from rasterio.io import MemoryFile
from rasterio.shutil import copy as copy_raster
from esa_biomass_gamma0.calibration import (
    calculate_gamma0,
    lut_pixel_coordinates,
    parse_annotation,
    read_lut_coordinates,
    resample_gamma_nought,
    window_coordinates,
)
from rasterio.windows import Window

CLIENT_ID = os.getenv("MAAP_CLIENT_ID", "offline-token")
CLIENT_SECRET = os.getenv("ESA_MAAP_CLIENT_SECRET")
ESA_STAC_API_URL = "https://catalog.maap.eo.esa.int/catalogue/"

logger = logging.getLogger(__name__)


async def fetch_assets(urls: dict[str, str], token: str) -> dict[str, bytes]:
    """Download the product's authenticated assets concurrently through obstore."""
    downloads = {}
    for name, url in urls.items():
        parsed_url = urlparse(url)
        store = HTTPStore(
            f"{parsed_url.scheme}://{parsed_url.netloc}",
            client_options={
                "default_headers": {"Authorization": f"Bearer {token}"},
                "timeout": "3m",
            },
        )
        logger.info("Starting download of %s", url)
        downloads[name] = obs.get_async(store, parsed_url.path.lstrip("/"))

    responses = await asyncio.gather(*downloads.values())
    contents = await asyncio.gather(*(response.bytes_async() for response in responses))
    logger.info("Completed %d asset downloads", len(contents))
    return dict(zip(downloads, map(bytes, contents), strict=True))


def cache_paths(item_id: str, cache_dir: Path) -> dict[str, Path]:
    """Return stable paths for an item's source assets."""
    safe_item_id = item_id.replace("/", "_")
    prefix = cache_dir / f"biomass__{safe_item_id}"
    return {
        "beta": prefix.with_name(f"{prefix.name}__beta0.tif"),
        "lut": prefix.with_name(f"{prefix.name}__lut.nc"),
        "annotation": prefix.with_name(f"{prefix.name}__annotation.xml"),
    }


def write_cached_asset(path: Path, contents: bytes) -> None:
    """Atomically write one downloaded asset to the local cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(contents)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def write_cog(
    path: Path,
    data: np.ndarray,
    gcps: list[GroundControlPoint],
    gcp_crs: object,
    descriptions: tuple[str, ...],
) -> None:
    """Write a GCP-referenced array as a deflate-compressed COG."""
    bands = data if data.ndim == 3 else data[np.newaxis]
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            height=bands.shape[1],
            width=bands.shape[2],
            count=bands.shape[0],
            dtype="float32",
            nodata=np.nan,
            transform=Affine.identity(),
        ) as raster:
            raster.write(bands)
            raster.descriptions = descriptions
            raster.gcps = (gcps, gcp_crs)
        with memory.open() as raster:
            copy_raster(
                raster,
                path,
                driver="COG",
                compress="DEFLATE",
                blocksize=512,
            )


def main(item_id: str, out_dir: Path, cache_dir: Path = Path("/tmp")) -> None:
    """Write full-granule diagnostics on their native GCP-referenced grids."""
    logger.info("Writing diagnostics to %s", out_dir)

    logger.info("Searching STAC for item %s", item_id)
    item = next(
        Client.open(ESA_STAC_API_URL)
        .search(ids=[item_id], collections=["BiomassLevel1b"], limit=1)
        .items()
    )
    if not item:
        raise ValueError(f"No BIOMASS L1B item found with id {item_id!r}")

    logger.info("Selected item %s", item.id)

    try:
        beta_url = item.assets["enclosure_tiff"].href
        lut_url = item.assets["enclosure_nc"].href
        annotation_url = item.assets["enclosure_annot_xml"].href
    except KeyError as error:
        raise ValueError(
            f"{item.id} is missing required asset {error.args[0]}"
        ) from error

    offline_token = os.getenv("ESA_OFFLINE_TOKEN")
    if not all((CLIENT_SECRET, offline_token)):
        raise ValueError("Missing MAAP_CLIENT_SECRET or ESA_OFFLINE_TOKEN env var")
    logger.info("Requesting a MAAP access token")
    response = requests.post(
        "https://iam.maap.eo.esa.int/realms/esa-maap/protocol/openid-connect/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": offline_token,
            "scope": "offline_access openid",
        },
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Failed to retrieve an access token from the IAM response")

    paths = cache_paths(item.id, cache_dir)
    asset_urls = {"beta": beta_url, "lut": lut_url, "annotation": annotation_url}
    missing_assets = {
        name: url for name, url in asset_urls.items() if not paths[name].exists()
    }
    if missing_assets:
        logger.info("Downloading %d missing asset(s)", len(missing_assets))
        for name, contents in asyncio.run(fetch_assets(missing_assets, token)).items():
            write_cached_asset(paths[name], contents)
    else:
        logger.info("Using cached source assets from %s", cache_dir)
    annotation = paths["annotation"].read_bytes()
    out_dir.mkdir(parents=True, exist_ok=True)

    lut_coordinates = read_lut_coordinates(paths["lut"])
    with Dataset(paths["lut"]) as dataset:
        lut = np.asarray(dataset["radiometry/gammaNought"][:], dtype="float32")
    logger.info("Read native LUT shape %s", lut.shape)
    metadata = parse_annotation(annotation)

    with rasterio.open(paths["beta"]) as beta_source:
        gcps, gcp_crs = beta_source.gcps
        beta = beta_source.read(masked=True).filled(np.nan).astype("float32")
        full_height, full_width = beta_source.height, beta_source.width
        logger.info(
            "Read Beta0: %d x %d, %d band(s), %d GCP(s)",
            full_width,
            full_height,
            beta_source.count,
            len(gcps),
        )

    beta_azimuth, beta_range = window_coordinates(
        metadata, Window(0, 0, full_width, full_height)
    )
    lut_rows, lut_cols = lut_pixel_coordinates(
        lut_coordinates, beta_azimuth, beta_range
    )
    logger.info(
        "Coordinate-aligned LUT coverage: Beta0 maps to native rows %.3f..%.3f "
        "of 0..%d and columns %.3f..%.3f of 0..%d; full-extent scaling would "
        "be wrong by up to %.3f rows and %.3f columns.",
        lut_rows[0],
        lut_rows[-1],
        lut_coordinates.shape[0] - 1,
        lut_cols[0],
        lut_cols[-1],
        lut_coordinates.shape[1] - 1,
        np.max(np.abs(lut_rows - np.linspace(0, lut.shape[0] - 1, full_height))),
        np.max(np.abs(lut_cols - np.linspace(0, lut.shape[1] - 1, full_width))),
    )
    lut_resampled = resample_gamma_nought(lut, lut_rows, lut_cols)
    gamma0 = calculate_gamma0(beta, lut_resampled)

    write_cog(
        out_dir / "beta0.tif",
        beta,
        gcps,
        gcp_crs,
        (
            "Beta0 amplitude HH",
            "Beta0 amplitude HV",
            "Beta0 amplitude VH",
            "Beta0 amplitude VV",
        ),
    )
    write_cog(
        out_dir / "gamma0.tif",
        gamma0,
        gcps,
        gcp_crs,
        ("Gamma0 HH", "Gamma0 HV", "Gamma0 VH", "Gamma0 VV"),
    )
    write_cog(
        out_dir / "lut_resampled.tif",
        lut_resampled,
        gcps,
        gcp_crs,
        ("gammaNought resampled to Beta0 radar pixels",),
    )

    lut_gcps = [
        GroundControlPoint(
            row=np.interp(gcp.row, np.arange(full_height), lut_rows),
            col=np.interp(gcp.col, np.arange(full_width), lut_cols),
            x=gcp.x,
            y=gcp.y,
            z=gcp.z,
            id=gcp.id,
            info=gcp.info,
        )
        for gcp in gcps
    ]
    write_cog(
        out_dir / "lut_native.tif",
        lut,
        lut_gcps,
        gcp_crs,
        ("gammaNought native LUT",),
    )

    logger.info("Wrote diagnostics for %s to %s", item.id, out_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_id", help="BIOMASS L1B STAC item id")
    parser.add_argument("--out-dir", type=Path, default=Path("diagnostics"))
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("/tmp"),
        help="Directory for cached source assets (default: /tmp)",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(message)s"
    )
    main(args.item_id, args.out_dir, args.cache_dir)

"""Tests for the checked-in scientific-validation release gate."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from esa_biomass_gamma0 import __version__ as PACKAGE_VERSION

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "validate_scientific_validation.py"
RELEASE_TAG = f"v{PACKAGE_VERSION}"


def _record(
    *, version: str = PACKAGE_VERSION, difference: float = 0.0009
) -> dict[str, object]:
    """Return one complete passing scientific-validation record."""
    return {
        "package_version": version,
        "windowed_vs_full_frame_gamma0_max_valid_pixel_difference": difference,
        "positional_checks": {
            "swath_edge": {"residual_m": 12.5, "result": "pass"},
            "swath_interior": {"residual_m": 4.0, "result": "pass"},
        },
    }


def _validate(
    tmp_path: Path, record: dict[str, object] | None, *, tag: str = RELEASE_TAG
) -> subprocess.CompletedProcess[str]:
    """Run the release gate against an isolated validation-record directory."""
    records = tmp_path / "records"
    records.mkdir()
    if record is not None:
        (records / f"{tag}.json").write_text(json.dumps(record), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--release-tag",
            tag,
            "--records-dir",
            str(records),
        ],
        text=True,
        capture_output=True,
    )


def test_release_gate_accepts_the_matching_passing_record(tmp_path: Path) -> None:
    """A complete matching record below the Gamma0 threshold passes."""
    result = _validate(tmp_path, _record())

    assert result.returncode == 0


@pytest.mark.parametrize(
    ("record", "tag", "error"),
    [
        (None, RELEASE_TAG, "missing"),
        (_record(version="stale"), RELEASE_TAG, "package_version"),
        (_record(difference=0.001), RELEASE_TAG, "below 0.001"),
        (
            _record()
            | {
                "positional_checks": {
                    "swath_edge": {"residual_m": 12.5, "result": "pass"},
                    "swath_interior": {"residual_m": 4.0, "result": "fail"},
                }
            },
            RELEASE_TAG,
            "swath_interior",
        ),
        (
            _record() | {"unexpected": "https://signed.example/test"},
            RELEASE_TAG,
            "fields",
        ),
        (_record(), PACKAGE_VERSION, "v<version>"),
    ],
)
def test_release_gate_rejects_missing_stale_or_failing_records(
    tmp_path: Path, record: dict[str, object] | None, tag: str, error: str
) -> None:
    """Missing, stale, malformed, and failing evidence blocks publication."""
    result = _validate(tmp_path, record, tag=tag)

    assert result.returncode == 1
    assert error in result.stderr

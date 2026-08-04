"""Validate the checked-in scientific evidence required for a release."""

import argparse
import json
import math
import sys
from pathlib import Path

GAMMA0_DIFFERENCE_LIMIT = 1e-3
RECORD_FIELDS = {
    "package_version",
    "windowed_vs_full_frame_gamma0_max_valid_pixel_difference",
    "positional_checks",
}
POSITIONAL_CHECK_FIELDS = {"residual_m", "result"}
POSITIONAL_CHECKS = {"swath_edge", "swath_interior"}


class ValidationError(Exception):
    """Raised when a scientific-validation record cannot gate a release."""


def _release_version(release_tag: str) -> str:
    """Return the safe package version encoded in a ``v<version>`` release tag."""
    version = release_tag.removeprefix("v")
    if (
        not version
        or release_tag == version
        or not all(character.isalnum() or character in ".+-" for character in version)
    ):
        raise ValidationError("release tag must use the form v<version>")
    return version


def _number(value: object, field: str) -> float:
    """Return one finite non-boolean numeric record field."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{field} must be a finite number")
    return number


def _require_fields(value: object, fields: set[str], context: str) -> dict[str, object]:
    """Return one record object with exactly the expected fields."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(f"{context} must contain exactly the required fields")
    return value


def validate(release_tag: str, records_dir: Path) -> None:
    """Raise ``ValidationError`` unless the release has passing scientific evidence."""
    version = _release_version(release_tag)
    record_path = records_dir / f"v{version}.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValidationError(
            f"scientific validation record is missing for {release_tag}"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(
            "scientific validation record is unreadable JSON"
        ) from error

    record = _require_fields(record, RECORD_FIELDS, "scientific validation record")
    if record["package_version"] != version:
        raise ValidationError("record package_version does not match the release tag")

    difference = _number(
        record["windowed_vs_full_frame_gamma0_max_valid_pixel_difference"],
        "windowed_vs_full_frame_gamma0_max_valid_pixel_difference",
    )
    if difference < 0 or difference >= GAMMA0_DIFFERENCE_LIMIT:
        raise ValidationError(
            "windowed_vs_full_frame_gamma0_max_valid_pixel_difference "
            "must be below 0.001"
        )

    checks = _require_fields(
        record["positional_checks"], POSITIONAL_CHECKS, "positional_checks"
    )
    for name, check in checks.items():
        check = _require_fields(check, POSITIONAL_CHECK_FIELDS, name)
        residual = _number(check["residual_m"], f"{name}.residual_m")
        if residual < 0 or check["result"] != "pass":
            raise ValidationError(f"{name} must report a passing non-negative residual")


def main() -> None:
    """Run the scientific-validation gate for one release tag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument(
        "--records-dir",
        type=Path,
        default=Path("dev-docs/scientific-validation"),
    )
    arguments = parser.parse_args()

    try:
        validate(arguments.release_tag, arguments.records_dir)
    except ValidationError as error:
        print(f"Scientific validation gate failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()

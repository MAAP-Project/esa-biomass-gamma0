"""Tests for the checked-in STAC metadata example."""

from pathlib import Path
import subprocess
import sys

from pystac import Collection

ROOT = Path(__file__).parents[1]
EXAMPLE_DIRECTORY = ROOT / "examples" / "stac"
SCRIPT = ROOT / "scripts" / "generate_stac_example.py"


def test_generator_reproduces_the_checked_in_stac_metadata(tmp_path: Path) -> None:
    """The generator produces valid metadata identical to the documented sample."""
    output_directory = tmp_path / "stac"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(output_directory)],
        check=True,
        cwd=ROOT,
    )

    collection = Collection.from_file(output_directory / "collection.json")
    item = next(collection.get_items())
    collection.validate()
    item.validate()

    expected_item = EXAMPLE_DIRECTORY / item.id / f"{item.id}.json"
    assert (output_directory / "collection.json").read_text() == (
        EXAMPLE_DIRECTORY / "collection.json"
    ).read_text()
    assert (output_directory / item.id / f"{item.id}.json").read_text() == (
        expected_item.read_text()
    )

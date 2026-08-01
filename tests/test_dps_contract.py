"""Contract tests for the MAAP DPS wrapper files."""

from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).parents[1]
STAGED_INPUTS = {
    "source_item": "File",
    "beta0_tiff": "File",
    "radiometry_lut": "File",
    "annotation_xml": "File",
}
SETTINGS = {"resolution": ("double", 25), "overwrite": ("boolean", False)}


def _load_yaml(name: str) -> dict[str, object]:
    """Load one repository-owned YAML contract document."""
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def _input_mapping(inputs: dict[str, object] | list[dict[str, object]]) -> dict[str, object]:
    """Normalize mapping and list input declarations to one mapping shape."""
    if isinstance(inputs, list):
        return {item["name"]: item for item in inputs}
    return inputs


def _input_types(inputs: dict[str, object]) -> dict[str, object]:
    """Return input names and their declared YAML types."""
    return {name: value["type"] for name, value in inputs.items()}


def test_dps_metadata_and_cwl_expose_matching_staged_inputs() -> None:
    """Algorithm metadata and both CWL graph nodes share the public inputs."""
    algorithm = _load_yaml("algorithm.yml")
    cwl = _load_yaml("esa-biomass-gamma0.cwl")
    workflow, tool = cwl["$graph"]
    algorithm_inputs = {item["name"]: item for item in algorithm["inputs"]}

    for declarations in (algorithm_inputs, workflow["inputs"], tool["inputs"]):
        inputs = _input_mapping(declarations)
        assert _input_types(inputs) == {
            **STAGED_INPUTS, **{name: setting[0] for name, setting in SETTINGS.items()}
        }
        for name, expected_type in STAGED_INPUTS.items():
            assert inputs[name]["type"] == expected_type
        for name, (expected_type, default) in SETTINGS.items():
            assert inputs[name]["type"] == expected_type
            assert inputs[name]["default"] == default


def test_cwl_returns_only_the_local_output_directory_without_network_access() -> None:
    """The CWL tool stages local Files and produces the local output Directory."""
    cwl = _load_yaml("esa-biomass-gamma0.cwl")
    workflow, tool = cwl["$graph"]

    assert workflow["outputs"] == {
        "output": {"type": "Directory", "outputSource": "process/output"}
    }
    assert tool["outputs"] == {
        "output": {"type": "Directory", "outputBinding": {"glob": "output"}}
    }
    assert tool["requirements"]["NetworkAccess"] == {"networkAccess": False}
    assert "secret" not in (ROOT / "esa-biomass-gamma0.cwl").read_text().lower()
    assert "credential" not in (ROOT / "esa-biomass-gamma0.cwl").read_text().lower()


def test_shell_wrappers_use_the_frozen_uv_runtime(tmp_path: Path) -> None:
    """The wrappers are syntactically valid and the runner creates local output."""
    for name in ("build.sh", "run.sh"):
        subprocess.run(["bash", "-n", ROOT / name], check=True)
    run = subprocess.run(
        [ROOT / "run.sh", "--help"], cwd=tmp_path, text=True, capture_output=True
    )

    assert run.returncode == 0
    assert (tmp_path / "output").is_dir()
    assert "--source-item" in run.stdout
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    runner = (ROOT / "run.sh").read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev" in build
    assert "uv run --frozen --no-dev" in runner
    assert "conda" not in build.lower()
    assert "conda" not in runner.lower()


def test_documentation_names_the_implemented_runtime_contract() -> None:
    """User-facing documentation names the installed CLI and output contract."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    specification = (
        ROOT / "dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md"
    ).read_text(encoding="utf-8")

    for document in (readme, specification):
        assert "process-gamma0" in document
        assert "uv run --frozen --no-dev" in document
        assert "./output" in document
        assert "study vector" not in document
    assert "remaining work is the package CLI" not in readme

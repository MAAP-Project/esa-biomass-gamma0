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
FETCH_INPUTS = {"item_id": "string"}
CONTRACTS = {
    "staged": ("esa_biomass_gamma0_staged", STAGED_INPUTS, False),
    "fetch": ("esa_biomass_gamma0_fetch", FETCH_INPUTS, True),
}


def _load_yaml(path: Path) -> dict[str, object]:
    """Load one repository-owned YAML contract document."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _input_mapping(
    inputs: dict[str, object] | list[dict[str, object]],
) -> dict[str, object]:
    """Normalize mapping and list input declarations to one mapping shape."""
    if isinstance(inputs, list):
        return {item["name"]: item for item in inputs}
    return inputs


def _input_types(inputs: dict[str, object]) -> dict[str, object]:
    """Return input names and their declared YAML types."""
    return {name: value["type"] for name, value in inputs.items()}


def test_dps_metadata_and_cwl_expose_matching_mode_inputs() -> None:
    """Each descriptor exposes only the inputs for its execution mode."""
    for mode, (algorithm_name, mode_inputs, _) in CONTRACTS.items():
        directory = ROOT / "dps" / mode
        algorithm = _load_yaml(directory / "algorithm.yml")
        cwl = _load_yaml(directory / f"esa-biomass-gamma0-{mode}.cwl")
        workflow, tool = cwl["$graph"]
        algorithm_inputs = {item["name"]: item for item in algorithm["inputs"]}

        assert algorithm["algorithm_name"] == algorithm_name
        for declarations in (algorithm_inputs, workflow["inputs"], tool["inputs"]):
            inputs = _input_mapping(declarations)
            assert _input_types(inputs) == mode_inputs
            for name, expected_type in mode_inputs.items():
                assert inputs[name]["type"] == expected_type


def test_cwl_returns_only_local_output_with_mode_specific_network_access() -> None:
    """Both tools return ``output`` while only fetch permits networking."""
    for mode, (_, _, network_access) in CONTRACTS.items():
        cwl_path = ROOT / "dps" / mode / f"esa-biomass-gamma0-{mode}.cwl"
        workflow, tool = _load_yaml(cwl_path)["$graph"]

        assert workflow["outputs"] == {
            "output": {"type": "Directory", "outputSource": "process/output"}
        }
        assert tool["outputs"] == {
            "output": {"type": "Directory", "outputBinding": {"glob": "output"}}
        }
        assert tool["requirements"]["NetworkAccess"] == {
            "networkAccess": network_access
        }

    staged_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "dps" / "staged").iterdir()
        if path.is_file()
    ).lower()
    assert "secret" not in staged_text
    assert "credential" not in staged_text


def test_shell_wrappers_use_mode_specific_frozen_uv_runtimes(tmp_path: Path) -> None:
    """Wrappers are syntactically valid and create only their local output directory."""
    for mode in CONTRACTS:
        directory = ROOT / "dps" / mode
        for name in ("build.sh", "run.sh"):
            subprocess.run(["bash", "-n", directory / name], check=True)
        run = subprocess.run(
            [directory / "run.sh", "--help"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
        )

        assert run.returncode == 0
        assert (tmp_path / "output").is_dir()
        expected_flag = "--source-item" if mode == "staged" else "item_id"
        assert expected_flag in run.stdout
        build = (directory / "build.sh").read_text(encoding="utf-8")
        runner = (directory / "run.sh").read_text(encoding="utf-8")
        assert "uv sync --frozen --no-dev" in build
        assert "uv run --frozen --no-dev" in runner
        assert ("--extra fetch" in build) is (mode == "fetch")
        assert ("--extra fetch" in runner) is (mode == "fetch")
        assert "conda" not in build.lower()
        assert "conda" not in runner.lower()


def test_root_descriptors_are_replaced_by_two_registered_packages() -> None:
    """The old single-package registration files no longer exist at repository root."""
    for name in (
        "algorithm.yml",
        "esa-biomass-gamma0.cwl",
        "build.sh",
        "run.py",
        "run.sh",
    ):
        assert not (ROOT / name).exists()


def test_documentation_names_both_dps_contracts_without_fetch_environment_secrets() -> (
    None
):
    """Users and maintainers can find both registration forms and their boundaries."""
    documents = {
        "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
        "AGENTS.md": (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
        "dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md": (
            ROOT / "dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md"
        ).read_text(encoding="utf-8"),
        "dev-docs/plans/2026-07-31-001-feat-source-package-dps-plan.md": (
            ROOT / "dev-docs/plans/2026-07-31-001-feat-source-package-dps-plan.md"
        ).read_text(encoding="utf-8"),
    }

    for text in documents.values():
        assert "esa_biomass_gamma0_staged" in text
        assert "esa_biomass_gamma0_fetch" in text
        assert "dps/staged/" in text
        assert "dps/fetch/" in text
    assert "MAAP().secrets.get_secret" in documents["README.md"]
    assert "environment secrets for fetch" not in documents["README.md"].lower()

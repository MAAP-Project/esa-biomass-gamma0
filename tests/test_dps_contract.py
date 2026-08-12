"""Contract tests for the MAAP DPS wrapper files."""

import json
from pathlib import Path
import re
import subprocess
import tomllib

import click
import yaml

ROOT = Path(__file__).parents[1]
PACKAGE_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
    "version"
]
STAGED_INPUTS = {
    "source_item": "File",
    "beta0_tiff": "File",
    "radiometry_lut": "File",
    "annotation_xml": "File",
}
FETCH_INPUTS = {"item_id": "string"}
CONTRACTS = {
    "staged": (
        "esa_biomass_gamma0_staged",
        STAGED_INPUTS,
        True,
        {"ramMin": 16, "coresMin": 8, "outdirMax": 20},
    ),
    "fetch": (
        "esa_biomass_gamma0_fetch",
        FETCH_INPUTS,
        True,
        {"ramMin": 16, "coresMin": 4, "outdirMax": 20},
    ),
}
PUBLIC_INPUT_LABELS = {
    "staged": {
        "source_item": "Source STAC Item",
        "beta0_tiff": "Beta0 TIFF",
        "radiometry_lut": "Radiometry LUT",
        "annotation_xml": "Annotation XML",
    },
    "fetch": {"item_id": "Source STAC Item ID"},
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


def test_cwl_exposes_only_the_inputs_for_its_execution_mode() -> None:
    """Each repository-owned CWL declares one mode-specific public interface."""
    for mode, (algorithm_name, mode_inputs, _, resources) in CONTRACTS.items():
        cwl = _load_yaml(ROOT / "dps" / mode / f"esa-biomass-gamma0-{mode}.cwl")
        workflow, tool = cwl["$graph"]
        workflow_inputs = _input_mapping(workflow["inputs"])
        tool_inputs = _input_mapping(tool["inputs"])

        assert workflow["id"] == algorithm_name
        assert workflow.get("label")
        assert workflow.get("doc")
        assert cwl["s:softwareVersion"] == PACKAGE_VERSION
        assert cwl["s:version"] == PACKAGE_VERSION
        assert cwl["s:codeRepository"] == (
            "https://github.com/MAAP-Project/esa-biomass-gamma0"
        )
        assert tool["requirements"]["ResourceRequirement"] == resources
        assert tool["requirements"]["DockerRequirement"]["dockerPull"] == (
            f"ghcr.io/maap-project/esa-biomass-gamma0-{mode}:v{PACKAGE_VERSION}"
        )
        assert _input_types(workflow_inputs) == mode_inputs
        assert _input_types(tool_inputs) == mode_inputs
        for name, expected_type in mode_inputs.items():
            assert workflow_inputs[name]["type"] == expected_type
            assert workflow_inputs[name]["label"] == PUBLIC_INPUT_LABELS[mode][name]
            assert workflow_inputs[name].get("doc")
            assert tool_inputs[name]["type"] == expected_type


def test_cwl_returns_only_local_output_with_declared_network_access() -> None:
    """Both tools return ``output`` with their required MAAP network policy."""
    for mode, (_, _, network_access, _) in CONTRACTS.items():
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
        subprocess.run(["bash", "-n", directory / "run.sh"], check=True)
        run = subprocess.run(
            [directory / "run.sh", "--help"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
        )

        assert run.returncode == 0
        assert (tmp_path / "output").is_dir()
        expected_flag = "--source-item" if mode == "staged" else "item_id"
        assert expected_flag in click.unstyle(run.stdout)
        runner = (directory / "run.sh").read_text(encoding="utf-8")
        assert "uv run --frozen --no-dev" in runner
        assert ("--extra fetch" in runner) is (mode == "fetch")
        assert "conda" not in runner.lower()


def test_direct_ogc_deployment_uses_only_tracked_cwl_contracts() -> None:
    """The retired descriptor/build path cannot become a second deployment contract."""
    for directory in (ROOT / "dps" / "staged", ROOT / "dps" / "fetch"):
        assert not (directory / "algorithm.yml").exists()
        assert not (directory / "build.sh").exists()


def test_release_please_owns_versioned_release_files() -> None:
    """Release Please updates package, CWL, and STAC example versions together."""
    config = json.loads((ROOT / "release-please-config.json").read_text())
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text())
    workflow = _load_yaml(ROOT / ".github" / "workflows" / "release-please.yml")
    extra_files = config["packages"]["."]["extra-files"]

    assert manifest == {".": PACKAGE_VERSION}
    assert config["include-v-in-tag"] is True
    assert config["always-update"] is True
    extra_files_by_path = {entry["path"]: entry for entry in extra_files}
    assert {entry["path"] for entry in extra_files} == {
        "dps/staged/esa-biomass-gamma0-staged.cwl",
        "dps/fetch/esa-biomass-gamma0-fetch.cwl",
        "examples/stac/collection.json",
        "examples/stac/gamma0-BIOMASS_EXAMPLE_001-32TPR/"
        "gamma0-BIOMASS_EXAMPLE_001-32TPR.json",
        "uv.lock",
    }
    assert {path: entry["type"] for path, entry in extra_files_by_path.items()} == {
        "dps/staged/esa-biomass-gamma0-staged.cwl": "generic",
        "dps/fetch/esa-biomass-gamma0-fetch.cwl": "generic",
        "examples/stac/collection.json": "json",
        "examples/stac/gamma0-BIOMASS_EXAMPLE_001-32TPR/"
        "gamma0-BIOMASS_EXAMPLE_001-32TPR.json": "json",
        "uv.lock": "generic",
    }
    for mode in CONTRACTS:
        cwl_path = f"dps/{mode}/esa-biomass-gamma0-{mode}.cwl"
        assert [
            entry["type"] for entry in extra_files if entry["path"] == cwl_path
        ] == ["generic"]

    assert extra_files_by_path["examples/stac/collection.json"]["jsonpath"] == (
        '$.providers[*]["processing:software"]["esa-biomass-gamma0"]'
    )
    assert (
        extra_files_by_path[
            "examples/stac/gamma0-BIOMASS_EXAMPLE_001-32TPR/"
            "gamma0-BIOMASS_EXAMPLE_001-32TPR.json"
        ]["jsonpath"]
        == '$.properties["processing:software"]["esa-biomass-gamma0"]'
    )
    assert workflow[True]["push"] == {"branches": ["main"]}
    [release_step] = workflow["jobs"]["release-please"]["steps"]
    assert release_step["id"] == "release-please"
    assert release_step["uses"].startswith(
        "googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7"
    )
    assert release_step["with"]["token"] == "${{ secrets.RELEASE_PLEASE_TOKEN }}"
    assert "GITHUB_TOKEN" not in str(release_step)

    lock = (ROOT / "uv.lock").read_text()
    assert f'version = "{PACKAGE_VERSION}" # x-release-please-version' in lock

    for mode in CONTRACTS:
        text = (ROOT / "dps" / mode / f"esa-biomass-gamma0-{mode}.cwl").read_text()
        assert text.count("x-release-please-version") == 2
        assert text.count("x-release-please-start-version") == 1
        assert text.count("x-release-please-end") == 1
        assert (
            f"dockerPull: ghcr.io/maap-project/esa-biomass-gamma0-{mode}:v"
            f"{PACKAGE_VERSION}"
        ) in text


def test_ci_builds_and_smoke_tests_both_images_without_maap_access() -> None:
    """Pull requests validate both runtime images; only main publishes latest."""
    workflow = _load_yaml(ROOT / ".github" / "workflows" / "ci.yml")
    container = workflow["jobs"]["container"]
    steps = container["steps"]

    assert workflow[True]["pull_request"] is None
    assert workflow[True]["push"] == {"branches": ["main"]}
    assert workflow["concurrency"]["cancel-in-progress"] == (
        "${{ github.ref == 'refs/heads/main' }}"
    )
    assert {entry["mode"] for entry in container["strategy"]["matrix"]["include"]} == {
        "staged",
        "fetch",
    }
    assert "docker build" in steps[1]["run"]
    assert "docker run" in steps[1]["run"]
    publish = workflow["jobs"]["publish-latest"]
    assert publish["if"] == "github.event_name == 'push'"
    assert publish["needs"] == "container"
    assert ":latest" in publish["steps"][1]["run"]
    assert container["permissions"] == {"contents": "read"}
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    assert "MAAP" not in str(workflow)


def test_quality_automation_is_pinned_and_covers_docs() -> None:
    """CI runs local quality gates and all Actions use immutable revisions."""
    ci = _load_yaml(ROOT / ".github" / "workflows" / "ci.yml")
    pages = _load_yaml(ROOT / ".github" / "workflows" / "docs.yml")
    dependabot = _load_yaml(ROOT / ".github" / "dependabot.yml")
    pre_commit = _load_yaml(ROOT / ".pre-commit-config.yaml")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    validate_script = ci["jobs"]["validate"]["steps"][-1]["run"]
    assert "pre-commit run --all-files" in validate_script
    assert "mkdocs build --strict" in validate_script
    assert pages[True]["push"] == {"branches": ["main"]}
    assert pages[True]["workflow_dispatch"] is None
    assert pages["jobs"]["deploy"]["environment"]["name"] == "github-pages"
    assert dependabot["version"] == 2
    assert {update["package-ecosystem"] for update in dependabot["updates"]} == {
        "uv",
        "github-actions",
    }
    assert {
        hook["id"] for repository in pre_commit["repos"] for hook in repository["hooks"]
    } == {"ruff", "ruff-format", "sync-with-uv"}
    assert pyproject["tool"]["ruff"]["target-version"] == "py313"
    assert {"pre-commit", "ruff"} <= {
        dependency.partition(">=")[0]
        for dependency in pyproject["dependency-groups"]["dev"]
    }
    assert {"mkdocs-material", "mkdocstrings[python]"} <= {
        dependency.partition(">=")[0]
        for dependency in pyproject["dependency-groups"]["docs"]
    }

    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        action_revisions = re.findall(
            r"^\s*(?:-\s+)?uses:\s+[^@\s]+@([^\s#]+)",
            path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        assert action_revisions
        assert all(
            re.fullmatch(r"[0-9a-f]{40}", revision) for revision in action_revisions
        )


def test_release_workflow_publishes_then_deploys_both_tracked_cwls() -> None:
    """A published release validates, publishes, then updates the two OGC processes."""
    workflow = _load_yaml(ROOT / ".github" / "workflows" / "release.yml")
    release = workflow[True]["release"]
    deploy = workflow["jobs"]["deploy"]
    deploy_script = deploy["steps"][-1]["run"]

    assert release == {"types": ["published"]}
    assert workflow["env"]["MAAP_OGC_PROCESSES_URL"] == (
        "https://api.maap-project.org/api/ogc/processes"
    )
    assert workflow["jobs"]["publish"]["needs"] == "validate"
    assert deploy["needs"] == "publish"
    assert deploy["environment"] == "production"
    assert "MAAP_TOKEN" in deploy_script
    assert "esa-biomass-gamma0-staged.cwl" in deploy_script
    assert "esa-biomass-gamma0-fetch.cwl" in deploy_script
    assert "--request POST" in deploy_script
    assert "--request PUT" in deploy_script
    assert "processPipelineLink.href" in deploy_script
    assert "GITHUB_STEP_SUMMARY" in deploy_script
    assert 'cat "$response"' not in deploy_script
    assert "latest" not in workflow["jobs"]["publish"]["steps"][-1]["with"]["tags"]
    assert "latest" not in deploy_script
    for job in workflow["jobs"].values():
        checkout = job["steps"][0]
        assert checkout["with"]["ref"] == "${{ github.event.release.tag_name }}"
        assert checkout["with"]["persist-credentials"] is False
    assert "git rev-parse HEAD" in str(workflow["jobs"])


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
        "DEVELOPMENT.md": (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8"),
    }

    for text in documents.values():
        assert "esa_biomass_gamma0_staged" in text
        assert "esa_biomass_gamma0_fetch" in text
        assert "dps/staged/" in text
        assert "dps/fetch/" in text
    assert "MAAP().secrets.get_secret" in documents["README.md"]
    assert "environment secrets for fetch" not in documents["README.md"].lower()
    for name in (
        "README.md",
        "AGENTS.md",
        "dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md",
        "DEVELOPMENT.md",
    ):
        assert "Release Please" in documents[name]
        assert "latest" in documents[name]
    assert "RELEASE_PLEASE_TOKEN" in documents["DEVELOPMENT.md"]
    assert "MAAP_TOKEN" in documents["DEVELOPMENT.md"]

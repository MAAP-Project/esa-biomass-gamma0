"""Tests for the staged, local, and fetch Gamma0 commands."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import click
import pytest
from typer.testing import CliRunner

from esa_biomass_gamma0.workflow import WorkflowResult

runner = CliRunner()


def _arguments(paths: dict[str, Path]) -> list[str]:
    """Return CLI arguments for one staged source."""
    return [
        "--source-item",
        str(paths["source_item"]),
        "--beta0-tiff",
        str(paths["beta0_tiff"]),
        "--radiometry-lut",
        str(paths["radiometry_lut"]),
        "--annotation-xml",
        str(paths["annotation_xml"]),
    ]


def test_staged_command_forwards_normalized_paths(
    staged_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The staged command adapts validated paths to the workflow API."""
    from esa_biomass_gamma0 import cli

    received: dict[str, object] = {}

    def process_source(**kwargs: object) -> WorkflowResult:
        received.update(kwargs)
        return WorkflowResult(1, 1, 0, 0)

    monkeypatch.setattr(cli, "process_source", process_source)
    output_root = tmp_path / "products"

    result = runner.invoke(
        cli.app,
        [
            "staged",
            *_arguments(staged_paths),
            "--output-root",
            str(output_root),
            "--window-padding-pixels",
            "7",
        ],
    )

    assert result.exit_code == 0
    assert received == {
        **{name: path.resolve() for name, path in staged_paths.items()},
        "output_root": output_root.resolve(),
        "window_padding_pixels": 7,
    }


def test_local_command_stages_then_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local command adapts cached Item-ID staging to the workflow API."""
    from esa_biomass_gamma0 import cli

    paths = {
        "source_item": tmp_path / "source-item.json",
        "beta": tmp_path / "beta0.tif",
        "lut": tmp_path / "lut.nc",
        "annotation": tmp_path / "annotation.xml",
    }
    staged: list[object] = []

    def stage(item_id: str, cache_dir: Path, *, refresh: bool) -> dict[str, Path]:
        staged.extend((item_id, cache_dir, refresh))
        return paths

    monkeypatch.setattr(cli, "stage_source", stage)
    received: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "process_source",
        lambda **kwargs: received.update(kwargs) or WorkflowResult(1, 1, 0, 0),
    )

    result = runner.invoke(cli.app, ["local", "source/item", "--refresh"])

    assert result.exit_code == 0
    assert staged == ["source/item", Path("/tmp/esa-biomass-gamma0").resolve(), True]
    assert received == {
        "source_item": paths["source_item"],
        "beta0_tiff": paths["beta"],
        "radiometry_lut": paths["lut"],
        "annotation_xml": paths["annotation"],
        "output_root": Path("output").resolve(),
        "window_padding_pixels": 64,
    }


def test_fetch_command_materializes_temporary_paths_then_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fetch command sends materialized paths directly to the workflow API."""
    from esa_biomass_gamma0 import cli, fetch

    materialized: dict[str, Path] = {}

    def materialize(item_id: str, destination: Path) -> dict[str, Path]:
        assert item_id == "source/item"
        materialized.update(
            {
                "source_item": destination / "source-item.json",
                "beta": destination / "beta0.tif",
                "lut": destination / "radiometry.nc",
                "annotation": destination / "annotation.xml",
            }
        )
        for path in materialized.values():
            path.write_bytes(b"staged")
        return materialized

    received: dict[str, object] = {}
    monkeypatch.setattr(fetch, "materialize_item", materialize)
    monkeypatch.setattr(
        cli,
        "process_source",
        lambda **kwargs: received.update(kwargs) or WorkflowResult(1, 1, 0, 0),
    )

    result = runner.invoke(
        cli.app, ["fetch", "source/item", "--output-root", str(tmp_path / "output")]
    )

    assert result.exit_code == 0
    assert received == {
        "source_item": materialized["source_item"],
        "beta0_tiff": materialized["beta"],
        "radiometry_lut": materialized["lut"],
        "annotation_xml": materialized["annotation"],
        "output_root": (tmp_path / "output").resolve(),
        "window_padding_pixels": 64,
    }
    assert not materialized["source_item"].exists()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--resolution", "25"], "No such option: --resolution"),
        (["--overwrite"], "No such option: --overwrite"),
        (["--window-padding-pixels", "-1"], "Invalid value"),
        (
            ["--source-item", "https://example.test/item.json?token=secret"],
            "local file",
        ),
    ],
)
def test_staged_command_rejects_invalid_inputs_before_processing(
    staged_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected: str,
) -> None:
    """Invalid staged command input fails without entering the workflow."""
    from esa_biomass_gamma0 import cli

    monkeypatch.setattr(
        cli,
        "process_source",
        lambda **_: pytest.fail("workflow must not run for invalid CLI input"),
    )

    result = runner.invoke(cli.app, ["staged", *_arguments(staged_paths), *arguments])

    assert result.exit_code == 2
    assert expected in click.unstyle(result.output)


def test_cli_help_and_errors_do_not_expose_credentials(
    staged_paths: dict[str, Path],
) -> None:
    """CLI-facing text contains neither credential settings nor signed URLs."""
    from esa_biomass_gamma0 import cli

    help_result = runner.invoke(cli.app, ["--help"])
    error_result = runner.invoke(
        cli.app,
        [
            "staged",
            *_arguments(staged_paths),
            "--source-item",
            "https://user:secret@example.test/item.json?token=secret",
        ],
    )

    assert help_result.exit_code == 0
    assert error_result.exit_code == 2
    assert "MAAP" not in help_result.output
    assert "token=secret" not in error_result.output
    assert "user:secret" not in error_result.output


def test_fetch_command_hides_materialization_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed fetch neither processes data nor exposes its secret value."""
    from esa_biomass_gamma0 import cli, fetch

    secret = "client-secret"

    def fail_materialization(*_: object) -> None:
        raise ValueError(secret)

    monkeypatch.setattr(fetch, "materialize_item", fail_materialization)
    monkeypatch.setattr(
        cli,
        "process_source",
        lambda **_: pytest.fail("workflow must not run after a failed fetch"),
    )

    result = runner.invoke(cli.app, ["fetch", "source/item"])

    assert result.exit_code == 1
    assert secret not in result.output
    assert secret not in caplog.text


def test_fetch_command_rejects_staged_inputs_without_importing_maap() -> None:
    """Fetch accepts only its Item ID and does not import MAAP while parsing."""
    sys.modules.pop("maap", None)
    sys.modules.pop("maap.maap", None)

    from esa_biomass_gamma0 import cli

    result = runner.invoke(
        cli.app, ["fetch", "source/item", "--source-item", "item.json"]
    )

    assert result.exit_code == 2
    assert "--source-item" in click.unstyle(result.output)
    assert "maap" not in sys.modules


@pytest.mark.parametrize("runner_path", ["dps/staged/run.py", "dps/fetch/run.py"])
def test_dps_runner_import_is_safe(runner_path: str) -> None:
    """Importing either MAAP adapter does not execute a production workflow."""
    path = Path(__file__).parents[1] / runner_path
    spec = spec_from_file_location("run", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

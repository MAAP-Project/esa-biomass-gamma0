"""Tests for the local staged-source command-line interface."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from esa_biomass_gamma0.workflow import WorkflowResult


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


def test_cli_forwards_normalized_staged_paths(
    staged_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI adapts validated arguments to the single workflow API."""
    from esa_biomass_gamma0 import cli

    received: dict[str, object] = {}

    def process_source(**kwargs: object) -> WorkflowResult:
        received.update(kwargs)
        return WorkflowResult(1, 1, 0, 0, 0)

    monkeypatch.setattr(cli, "process_source", process_source)
    output_root = tmp_path / "products"

    assert cli.main(
        [
            *_arguments(staged_paths),
            "--output-root",
            str(output_root),
            "--resolution",
            "25",
            "--overwrite",
            "--window-padding-pixels",
            "7",
        ]
    ) == 0

    assert received == {
        **{name: path.resolve() for name, path in staged_paths.items()},
        "output_root": output_root.resolve(),
        "resolution": 25.0,
        "overwrite": True,
        "window_padding_pixels": 7,
    }


def test_cli_defaults_match_the_staged_product_contract(
    staged_paths: dict[str, Path]
) -> None:
    """The direct runner defaults to the fixed 25 m output contract."""
    from esa_biomass_gamma0.cli import parse_args

    arguments = parse_args(_arguments(staged_paths))

    assert arguments.output_root == Path("output").resolve()
    assert arguments.resolution == 25.0
    assert arguments.overwrite is False
    assert arguments.window_padding_pixels == 64
    assert arguments.log_level == "INFO"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--resolution", "30"], "resolution must be 25"),
        (["--window-padding-pixels", "-1"], "window padding must be non-negative"),
        (["--source-item", "https://example.test/item.json?token=secret"], "local file"),
    ],
)
def test_cli_rejects_invalid_inputs_before_processing(
    staged_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected: str,
) -> None:
    """Invalid local arguments fail without entering the workflow."""
    from esa_biomass_gamma0 import cli

    monkeypatch.setattr(
        cli,
        "process_source",
        lambda **_: pytest.fail("workflow must not run for invalid CLI input"),
    )

    with pytest.raises(SystemExit) as error:
        cli.main([*_arguments(staged_paths), *arguments])

    assert error.value.code == 2
    assert expected in capsys.readouterr().err


def test_cli_help_and_errors_do_not_expose_credentials(
    staged_paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI-facing text contains neither credential settings nor signed URLs."""
    from esa_biomass_gamma0 import cli

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    help_text = capsys.readouterr().out

    with pytest.raises(SystemExit):
        cli.main(
            [
                *_arguments(staged_paths),
                "--source-item",
                "https://user:secret@example.test/item.json?token=secret",
            ]
        )
    error_text = capsys.readouterr().err

    assert "MAAP" not in help_text
    assert "token=secret" not in error_text
    assert "user:secret" not in error_text


def test_root_runner_import_is_safe() -> None:
    """Importing the root adapter does not execute the production workflow."""
    path = Path(__file__).parents[1] / "run.py"
    spec = spec_from_file_location("run", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

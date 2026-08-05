"""Release-deployment completion contract."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_release_deploy_waits_for_maap_deployment_jobs() -> None:
    """The release cannot pass while MAAP deployment remains incomplete."""
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    script = workflow["jobs"]["deploy"]["steps"][-1]["run"]

    assert workflow["env"]["MAAP_OGC_DEPLOYMENT_JOBS_URL"] == (
        "https://api.maap-project.org/api/ogc/deploymentJobs"
    )
    assert "wait_for_deployment" in script
    assert '[ "$status" != "202" ] || wait_for_deployment' in script
    assert "MAAP deployment timed out" in script
    assert "MAAP deployment failed" in script

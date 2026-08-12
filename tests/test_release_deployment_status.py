"""Release-deployment completion contract."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
ACCEPTED_DEPLOYMENT_RESPONSE = {
    "links": [
        {
            "href": "/ogc/deploymentJobs/90",
            "rel": "monitor",
            "type": "application/json",
        }
    ],
    "processPipelineLink": {
        "href": "https://repo.maap-project.org/root/deploy-ogc-hysds/-/pipelines/19390",
        "type": "text/html",
    },
}


def test_release_deploy_waits_for_maap_deployment_jobs() -> None:
    """The release cannot pass while MAAP deployment remains incomplete."""
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    script = workflow["jobs"]["deploy"]["steps"][-1]["run"]

    assert workflow["env"]["MAAP_OGC_DEPLOYMENT_JOBS_URL"] == (
        "https://api.maap-project.org/api/ogc/deploymentJobs"
    )
    assert "monitor_deployments" in script
    assert '[ "$status" != "202" ] || pending_workflows+=("$workflow")' in script
    assert script.index("deploy dps/staged") < script.index("deploy dps/fetch")
    assert script.index("deploy dps/fetch") < script.index(
        'monitor_deployments "${pending_workflows[@]}"'
    )
    assert "MAAP deployment timed out" in script
    assert "MAAP deployment failed" in script
    assert ".links[]?" in script
    assert (
        'deployment_url="${MAAP_OGC_DEPLOYMENT_JOBS_URL%/deploymentJobs}$deployment_url"'
        in script
    )
    assert 'select(.rel == "monitor" and .type == "application/json")' in script


def test_accepted_response_uses_json_monitor_link() -> None:
    """The API monitor link wins over the human-facing pipeline link."""
    monitor_link = next(
        link
        for link in ACCEPTED_DEPLOYMENT_RESPONSE["links"]
        if link["rel"] == "monitor" and link["type"] == "application/json"
    )

    assert monitor_link["href"] == "/ogc/deploymentJobs/90"
    assert ACCEPTED_DEPLOYMENT_RESPONSE["processPipelineLink"]["type"] == "text/html"

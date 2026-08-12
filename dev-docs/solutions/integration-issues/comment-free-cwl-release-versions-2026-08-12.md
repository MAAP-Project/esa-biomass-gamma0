---
title: Keep CWL release versions comment-free
date: 2026-08-12
category: integration-issues
module: OGC deployment
problem_type: integration_issue
component: tooling
symptoms:
  - "Docker rejects the MAAP deployment image with invalid reference format"
  - "Deployment logs show TAG includes # x-release-please-version"
root_cause: config_error
resolution_type: config_change
severity: high
tags: [cwl, release-please, maap, docker, deployment]
---

# Keep CWL Release Versions Comment-Free

## Problem
MAAP's OGC deployment adapter used the full `s:version` line as a Docker tag. An inline Release Please marker became part of the tag and caused deployment to fail.

## Symptoms
- The deployment job logged `TAG` as `0.2.1 # x-release-please-version`.
- Docker failed with `invalid reference format`.

## What Didn't Work
- Inline `# x-release-please-version` markers update CWL metadata correctly for Release Please, but MAAP does not discard the YAML comment when extracting the version.

## Solution
Use Release Please block markers around the metadata so the values remain clean YAML scalars:

```yaml
# x-release-please-start-version
s:softwareVersion: 0.2.1
s:version: 0.2.1
# x-release-please-end
```

Keep the release workflow's checks exact:

```sh
grep -Fqx "s:version: $version" "$workflow"
```

## Why This Works
Release Please updates semantic versions inside a marked block. MAAP receives `0.2.1`, without the marker text, when it constructs the Docker image reference.

## Prevention
- Keep automation comments on their own lines when MAAP consumes a CWL scalar.
- Test that release-version scalars contain no inline Release Please marker.

## Related Issues
- https://github.com/MAAP-Project/esa-biomass-gamma0/pull/23

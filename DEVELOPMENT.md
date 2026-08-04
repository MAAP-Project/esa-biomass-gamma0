# Development and MAAP deployment

## Deployment model

This repository deploys only direct OGC Application Packages. The tracked CWL
files are the authoritative MAAP interfaces; there is no `algorithm.yml` or
MAAP package-build path.

| Process ID | CWL | Container image |
| --- | --- | --- |
| `esa_biomass_gamma0_staged` | `dps/staged/esa-biomass-gamma0-staged.cwl` | `ghcr.io/maap-project/esa-biomass-gamma0-staged` |
| `esa_biomass_gamma0_fetch` | `dps/fetch/esa-biomass-gamma0-fetch.cwl` | `ghcr.io/maap-project/esa-biomass-gamma0-fetch` |

The staged CWL accepts four `File` inputs and disables network access. The
fetch CWL accepts only `item_id` and enables network access. Both return an
`output` `Directory`.

## Release deployment

Pull requests validate the test suite, both CWLs, and both container variants.
They never publish images or contact MAAP. Merges to `main` also publish
`latest` staged and fetch images to GHCR for development only; tracked CWLs
never reference `latest`.

[Release Please](.github/workflows/release-please.yml) runs after conventional
commits merge to `main`. It opens or updates a release PR with the package
version, frozen `uv.lock` package entry, both annotated hand-maintained CWLs,
and `CHANGELOG.md`. Review that PR like any other change. Merging it creates a `v<package-version>` GitHub
Release, which triggers [the deployment workflow](.github/workflows/release.yml):

1. it checks out the release tag and verifies the recorded MAAP-backed scientific evidence;
2. it verifies the package/CWL/image-tag contract, validates both CWLs, and runs the test suite;
3. it publishes immutable staged and fetch `v<version>` images; and
4. it registers each release-commit-pinned raw CWL URL at
   `https://api.maap-project.org/api/ogc/processes`, updating an existing process
   when MAAP returns HTTP 409.

Before merging a Release Please PR, add the matching versioned record described
in [`dev-docs/scientific-validation/README.md`](dev-docs/scientific-validation/README.md).
The gate runs without MAAP credentials and blocks image publication and
registration when the record is missing, stale, malformed, has a non-passing
positional check, or reports a Gamma0 difference of `>= 1e-3`.

Set `RELEASE_PLEASE_TOKEN` as a fine-grained repository token with Contents and
Pull requests read/write access. It lets Release Please create the GitHub Release
that starts deployment. Set `MAAP_TOKEN` as a repository or `production`
environment secret; deployment sends it only as the `proxy-ticket` request
header and does not log it.

After the first image publication, make both GHCR packages public (or otherwise
ensure MAAP workers can pull them without credentials) before submitting MAAP
jobs. MAAP receives only the CWL URL; it does not build images or clone this
repository. Do not retag an image MAAP already uses. Correct the release PR or
version contract, create a new release, and rerun its deployment workflow if
recovery is needed.

Run the release checks locally when changing a CWL:

```bash
uvx --from cwltool cwltool --validate dps/staged/esa-biomass-gamma0-staged.cwl
uvx --with pyyaml --from ogc-ap-validator ap-validator --detail errors \
  dps/staged/esa-biomass-gamma0-staged.cwl
uvx --from cwltool cwltool --validate dps/fetch/esa-biomass-gamma0-fetch.cwl
uvx --with pyyaml --from ogc-ap-validator ap-validator --detail errors \
  dps/fetch/esa-biomass-gamma0-fetch.cwl
uv run --frozen --group dev pytest
```

## Fetch secrets

Before submitting an `esa_biomass_gamma0_fetch` job, configure these MAAP
secrets for the job identity:

- `ESA_MAAP_CLIENT_SECRET`
- `ESA_OFFLINE_TOKEN`

The fetch runtime reads them with `MAAP().secrets.get_secret`. Do not put their
values in CWL files, GitHub Actions configuration, or job inputs.

# Scientific validation release records

A GitHub Release cannot publish images or register MAAP processes until its
matching record passes the release gate. This is a checked-in summary of
MAAP-backed validation, not a GitHub Actions job: do the processing from an
approved MAAP environment before opening the Release Please PR.

For release `v<version>`, add
`dev-docs/scientific-validation/v<version>.json` with exactly this shape:

```json
{
  "package_version": "<version>",
  "windowed_vs_full_frame_gamma0_max_valid_pixel_difference": <number below 0.001>,
  "positional_checks": {
    "swath_edge": {"residual_m": <non-negative number>, "result": "pass"},
    "swath_interior": {"residual_m": <non-negative number>, "result": "pass"}
  }
}
```

Before marking each positional check as passing, compare the edge and interior
tiles with independent map features and the intended spectral dataset. Review
the full-frame diagnostic comparison with matching calculation and bilinear
resampling conventions. The Gamma0 value is the maximum difference across
valid pixels and must be strictly below `1e-3`.

Keep only these aggregate results in the record. Do not include source Item
IDs, asset URLs, signed URLs, tokens, credentials, command output, or source
files. The release checker rejects fields beyond the format above and runs
without MAAP credentials. Check a pending release locally with:

```bash
uv run --frozen python scripts/validate_scientific_validation.py \
  --release-tag v<version>
```

Review the record with the Release Please version change. A missing record, a
version mismatch, malformed results, a non-passing positional check, or a
Gamma0 difference of `>= 1e-3` stops the release before image publication and
MAAP registration.

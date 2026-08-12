# ESA BIOMASS Gamma0 MGRS DPS

This package converts ESA BIOMASS Level-1B Beta0 amplitudes into fixed-grid,
25 m MGRS Beta0 and linear Gamma0 products for MAAP.

## Interfaces

- `esa_biomass_gamma0_staged` processes a staged source STAC Item, Beta0 TIFF,
  radiometry LUT, and annotation XML.
- `esa_biomass_gamma0_fetch` receives an Item ID, materializes those files in
  the MAAP runtime, and invokes the same staged workflow.

Each accepted source-item and MGRS-tile pair produces nine scientific COGs, an
RGB thumbnail, and a STAC Item.

## Further reading

- [Usage and local development](https://github.com/MAAP-Project/esa-biomass-gamma0#readme)
- [Release and MAAP deployment](https://github.com/MAAP-Project/esa-biomass-gamma0/blob/main/DEVELOPMENT.md)
- [Workflow specification](https://github.com/MAAP-Project/esa-biomass-gamma0/blob/main/dev-docs/specs/gamma0-mgrs-utm-stac-workflow.md)
- [Level 1b georeferencing notebook](georeferencing.ipynb)
- [Gamma0 Correction Algorithm proof-of-concept notebook](poc.ipynb)

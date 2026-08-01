"""Package-boundary tests."""

import esa_biomass_gamma0


def test_package_exposes_a_version() -> None:
    """Importing the package exposes installed package provenance."""
    assert esa_biomass_gamma0.__version__

"""Staged ESA BIOMASS Gamma0 processing package."""

from importlib.metadata import version

__version__ = version("esa-biomass-gamma0")

from esa_biomass_gamma0.workflow import process_source

__all__ = ["__version__", "process_source"]

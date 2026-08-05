#!/usr/bin/env python3
"""MAAP adapter for staged local Gamma0 inputs."""

import sys

from esa_biomass_gamma0.cli import app


if __name__ == "__main__":
    app(["staged", *sys.argv[1:]])

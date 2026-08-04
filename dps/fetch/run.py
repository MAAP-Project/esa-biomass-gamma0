#!/usr/bin/env python3
"""MAAP adapter for Item-ID Gamma0 fetch jobs."""

import sys

from esa_biomass_gamma0.cli import app


if __name__ == "__main__":
    app(["fetch", *sys.argv[1:]])

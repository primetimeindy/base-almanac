"""Fallback entry point: `python -m almanac ...` without an installed console script."""
from almanac.cli import main

raise SystemExit(main())

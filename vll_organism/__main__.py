"""Enables `python -m vll_organism ...` directly (without this file, `-m`
on a package with no __main__.py fails with "is a package and cannot be
directly executed" -- this is what was missing before)."""
from .cli import main

if __name__ == "__main__":
    main()

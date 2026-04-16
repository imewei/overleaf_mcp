# Makefile for overleaf_mcp — housekeeping targets only.
#
# Application functionality lives in the Python package (invoked as the
# MCP server via `overleaf-mcp` or through Claude Desktop). This file
# covers local cleanup operations you'd run between dev sessions or
# before committing.
#
# Default target is `help` — running `make` with no args never deletes
# anything. Destructive targets (clean, clean-all) are opt-in by name.

.PHONY: help clean clean-all

# --- help -----------------------------------------------------------------

help:
	@echo "overleaf_mcp — housekeeping targets"
	@echo ""
	@echo "  make clean      Remove build, test, lint, and packaging artifacts."
	@echo "                  Always safe; everything regenerated on next run."
	@echo ""
	@echo "  make clean-all  clean + remove .venv/ and overleaf_cache/."
	@echo "                  overleaf_cache/ will be re-cloned from Overleaf on"
	@echo "                  next tool use — any uncommitted local changes"
	@echo "                  there will be lost."
	@echo ""
	@echo "Never cleaned (user data):"
	@echo "  overleaf_config.json   — project IDs + Git tokens"
	@echo "  docs/                  — plans and documentation you wrote"

# --- clean ----------------------------------------------------------------
# Targets every auto-generated cache or build artifact. Running this
# leaves all source, tests, docs, config, and the local Overleaf clones
# untouched.

clean:
	@echo ">> Removing Python bytecode caches"
	@find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
	@echo ">> Removing test + lint + type-checker caches"
	@rm -rf .pytest_cache .mypy_cache .ruff_cache
	@rm -rf .coverage coverage.xml htmlcov
	@echo ">> Removing packaging + build artifacts"
	@rm -rf build dist
	@rm -rf src/overleaf_mcp.egg-info src/*.egg-info *.egg-info
	@echo ">> Removing uv project cache"
	@rm -rf .uv
	@echo "clean: done."

# --- clean-all ------------------------------------------------------------
# Pristine-checkout reset. Everything clean does, plus the virtual env
# and the local Overleaf clone cache. After this, you'll need to run
# `uv sync --extra dev` (or `pip install -e ".[dev]"`) before any tool
# works again.

clean-all: clean
	@echo ">> Removing virtual environments"
	@rm -rf .venv venv env ENV
	@echo ">> Removing local Overleaf clones (overleaf_cache/)"
	@rm -rf overleaf_cache
	@echo "clean-all: done. Reinstall with: uv sync --extra dev"

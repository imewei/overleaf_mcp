# Makefile for overleaf_mcp — local development targets.
#
# Application functionality lives in the Python package (invoked as the
# MCP server via `overleaf-mcp` or through Claude Desktop). This file
# covers local dev workflows: pre-push verification, lint/type/test
# wrappers, housekeeping, and pre-commit hook install.
#
# Default target is `help` — running `make` with no args never deletes
# anything. Destructive targets (clean, clean-all) are opt-in by name.

.PHONY: help clean clean-all \
        format lint type-check test \
        verify verify-fast install-hooks

# --- help -----------------------------------------------------------------

help:
	@echo "overleaf_mcp — development targets"
	@echo ""
	@echo "Pre-push verification:"
	@echo "  make verify        Full local CI: lint + type + bandit + tests."
	@echo "                     Run this before 'git push'."
	@echo "  make verify-fast   Quick: lint + type only (no tests)."
	@echo ""
	@echo "Quality:"
	@echo "  make format        Auto-fix with ruff (lint --fix + format)."
	@echo "  make lint          Ruff lint + format check (read-only)."
	@echo "  make type-check    Mypy on src/overleaf_mcp."
	@echo "  make test          Pytest."
	@echo ""
	@echo "Setup:"
	@echo "  make install-hooks Install pre-commit git hooks."
	@echo ""
	@echo "Housekeeping:"
	@echo "  make clean         Remove build, test, lint, and packaging artifacts."
	@echo "                     Always safe; everything regenerated on next run."
	@echo "  make clean-all     clean + remove .venv/ and overleaf_cache/."
	@echo "                     overleaf_cache/ will be re-cloned from Overleaf on"
	@echo "                     next tool use — any uncommitted local changes"
	@echo "                     there will be lost."
	@echo ""
	@echo "Never cleaned (user data):"
	@echo "  overleaf_config.json   — project IDs + Git tokens"
	@echo "  docs/                  — plans and documentation you wrote"

# --- quality wrappers -----------------------------------------------------
# Thin wrappers around the same commands CI runs (.github/workflows/ci.yml).
# Keep invocations identical so "local clean" ⇒ "CI clean".

format:
	@echo ">> Ruff lint --fix + format"
	@uv run ruff check --fix src/overleaf_mcp tests
	@uv run ruff format src/overleaf_mcp tests

lint:
	@echo ">> Ruff lint + format check"
	@uv run ruff check src/overleaf_mcp tests
	@uv run ruff format --check src/overleaf_mcp tests

type-check:
	@echo ">> Mypy (src/overleaf_mcp)"
	@uv run mypy src/overleaf_mcp

test:
	@echo ">> Pytest"
	@uv run pytest

# --- verify ---------------------------------------------------------------
# Full local CI: run before pushing. Patterned on rheojax/Makefile's
# step-marked verify target. mypy is a HARD gate here — pyproject.toml
# documents "mypy is clean for our own code", so a regression should
# block the push. bandit is advisory (matches CI: continue-on-error).

verify:
	@echo "======================================"
	@echo "  FULL LOCAL CI VERIFICATION"
	@echo "======================================"
	@echo ""
	@echo "Step 1/4: Ruff lint + format check"
	@uv run ruff check src/overleaf_mcp tests || { echo "Lint failed."; exit 1; }
	@uv run ruff format --check src/overleaf_mcp tests || { echo "Format check failed — run 'make format'."; exit 1; }
	@echo ""
	@echo "Step 2/4: Mypy type check"
	@uv run mypy src/overleaf_mcp || { echo "Type check failed."; exit 1; }
	@echo ""
	@echo "Step 3/4: Bandit security lint (advisory)"
	@uv run bandit -r src/overleaf_mcp -c pyproject.toml || echo "(advisory: bandit reported findings — review and proceed.)"
	@echo ""
	@echo "Step 4/4: Pytest"
	@uv run pytest || { echo "Tests failed."; exit 1; }
	@echo ""
	@echo "======================================"
	@echo "  ALL CHECKS PASSED — SAFE TO PUSH"
	@echo "======================================"

verify-fast:
	@echo "======================================"
	@echo "  QUICK LOCAL CI VERIFICATION"
	@echo "======================================"
	@echo ""
	@echo "Step 1/2: Ruff lint + format check"
	@uv run ruff check src/overleaf_mcp tests || { echo "Lint failed."; exit 1; }
	@uv run ruff format --check src/overleaf_mcp tests || { echo "Format check failed — run 'make format'."; exit 1; }
	@echo ""
	@echo "Step 2/2: Mypy type check"
	@uv run mypy src/overleaf_mcp || { echo "Type check failed."; exit 1; }
	@echo ""
	@echo "======================================"
	@echo "  QUICK CHECKS PASSED"
	@echo "======================================"

# --- pre-commit hooks -----------------------------------------------------

install-hooks:
	@echo ">> Installing pre-commit hooks"
	@uv run pre-commit install
	@echo "Done. Next 'git commit' runs ruff + mypy + bandit on staged files."
	@echo "Use 'make verify' before pushing for full local CI."

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

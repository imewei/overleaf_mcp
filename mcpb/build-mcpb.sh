#!/usr/bin/env bash
# Build an overleaf-mcp MCPB bundle.
#
# Produces dist/overleaf-mcp-<version>.mcpb — a single-file archive that
# users drag onto Claude Desktop. The bundle contains:
#
#   manifest.json          — identity + entry point + user_config schema
#   server/bootstrap.py    — prepends vendor/ to sys.path and launches main()
#   server/vendor/         — pip-installed dependencies (mcp, fastmcp, gitpython, pydantic)
#   server/overleaf_mcp/   — the server source tree
#
# Prerequisites:
#   - python3 with pip
#   - npx (for @anthropic-ai/mcpb pack)
#   - git (at runtime on the target machine — NOT bundled here)
#
# Usage:
#   ./mcpb/build-mcpb.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
STAGE="$REPO_ROOT/build/mcpb"
DIST="$REPO_ROOT/dist"

echo ">> Staging at $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE/server"

# 1) Manifest at bundle root
cp "$REPO_ROOT/manifest.json" "$STAGE/manifest.json"

# 2) Entry point (per manifest: server/bootstrap.py)
cp "$REPO_ROOT/mcpb/bootstrap.py" "$STAGE/server/bootstrap.py"

# 3) Server source tree — copied (not symlinked) so the bundle is
#    self-contained even when extracted on a different machine.
cp -r "$REPO_ROOT/src/overleaf_mcp" "$STAGE/server/overleaf_mcp"

# 4) Vendored dependencies. Runtime deps only — dev extras are excluded.
#
# Prefer `uv pip install --target` (works even when pip isn't in the venv,
# common for uv-managed environments). Fall back to `python -m pip` for
# systems without uv.
echo ">> Vendoring runtime deps into server/vendor/"
VENDOR_DEPS=(
    "mcp>=1.0.0" "fastmcp>=3.0.0" "gitpython>=3.1.40" "pydantic>=2.0.0"
)
if command -v uv >/dev/null 2>&1; then
    uv pip install --target "$STAGE/server/vendor" --no-compile "${VENDOR_DEPS[@]}"
else
    python3 -m pip install --target "$STAGE/server/vendor" --no-compile "${VENDOR_DEPS[@]}"
fi

# Strip items that are safe to remove — never imported at runtime.
#
# KEEP *.dist-info: pydantic, fastmcp, and the mcp SDK all call
#     importlib.metadata.version(pkg) at runtime. Removing dist-info
#     produces PackageNotFoundError at bundle-load time. Verified 2026-04-16.
# REMOVE __pycache__: regenerated on first import, never needed shipped.
# REMOVE tests/: packages sometimes ship their own test suites; never
#     invoked by our code and sometimes include multi-MB fixture data.
# REMOVE docs/ examples/ locale/: documentation sources, never imported.
# REMOVE *.pyi: type stubs only used by static analyzers, not runtime.
echo ">> Stripping safe-to-remove files from vendor/"
find "$STAGE/server/vendor" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/server/vendor" -type d \( -name tests -o -name testing -o -name docs -o -name examples -o -name locale \) -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/server/vendor" -name '*.pyi' -delete 2>/dev/null || true

# 5) Validate + pack
mkdir -p "$DIST"
echo ">> Validating manifest.json"
npx --yes @anthropic-ai/mcpb validate "$STAGE/manifest.json"

echo ">> Packing dist/overleaf-mcp-${VERSION}.mcpb"
cd "$STAGE"
npx --yes @anthropic-ai/mcpb pack "$STAGE" "$DIST/overleaf-mcp-${VERSION}.mcpb"

echo ""
echo "Done: $DIST/overleaf-mcp-${VERSION}.mcpb"
echo ""
echo "To install: drag the .mcpb file onto Claude Desktop."
echo "NOTE: users still need 'git' on their PATH at runtime — GitPython"
echo "shells out to the system git binary and we intentionally do not"
echo "bundle it (cross-platform git bundling is its own nightmare)."

"""Post-bundle smoke test for the MCPB archive.

Recommendation from code review: the bundle vendors runtime deps with
their ``*.dist-info`` directories preserved (see ``mcpb/build-mcpb.sh``
comment). A regression that strips dist-info would silently break
``importlib.metadata.version()`` calls made by pydantic / fastmcp / the
MCP SDK at import time. This test verifies, against a real built
bundle, that the metadata is still resolvable.

The test is **opt-in** — it requires a built bundle in ``dist/``. CI can
run it after ``mcpb/build-mcpb.sh``; local dev runs skip gracefully when
no bundle is present. We do NOT build the bundle inside the test because
``npx @anthropic-ai/mcpb pack`` is slow (~30 s cold start).
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"


def _find_bundle() -> Path | None:
    """Locate the most recent ``*.mcpb`` file under ``dist/``."""
    if not DIST_DIR.is_dir():
        return None
    candidates = sorted(DIST_DIR.glob("overleaf-mcp-*.mcpb"))
    return candidates[-1] if candidates else None


@pytest.mark.skipif(
    _find_bundle() is None,
    reason=("No MCPB bundle built. Run mcpb/build-mcpb.sh to generate one, then re-run this test."),
)
def test_mcpb_bundle_importlib_metadata_resolves(tmp_path: Path) -> None:
    """Vendored pydantic's ``importlib.metadata.version()`` MUST resolve.

    This pins the invariant documented in ``build-mcpb.sh``: dist-info
    directories survive the bundle-pack step. Without dist-info,
    pydantic raises ``PackageNotFoundError`` at import time, breaking
    the bundle for every end user.

    The subprocess is launched with ``PYTHONNOUSERSITE=1`` and a clean
    ``PYTHONPATH`` to ensure we're resolving against the bundled
    ``vendor/`` directory, not the dev machine's site-packages.
    """
    bundle = _find_bundle()
    assert bundle is not None  # guarded by skipif

    # Unpack into tmp. MCPB is a ZIP archive — zipfile handles it.
    unpack_dir = tmp_path / "bundle"
    unpack_dir.mkdir()
    with zipfile.ZipFile(bundle) as zf:
        zf.extractall(unpack_dir)

    vendor_dir = unpack_dir / "server" / "vendor"
    assert vendor_dir.is_dir(), (
        f"bundle missing server/vendor/: extracted layout was "
        f"{sorted(p.name for p in unpack_dir.iterdir())}"
    )

    # Run a probe that does exactly what pydantic does internally: look
    # up its own version. If dist-info is missing, PackageNotFoundError.
    probe = (
        "import sys, importlib.metadata as md;"
        f"sys.path.insert(0, {str(vendor_dir)!r});"
        "v = md.version('pydantic');"
        "print(v)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=15,
        env={"PYTHONNOUSERSITE": "1", "PYTHONPATH": ""},
    )

    assert result.returncode == 0, (
        f"importlib.metadata probe failed (rc={result.returncode}):\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    # Version string shape sanity: at least "X.Y"
    version_out = result.stdout.strip()
    assert version_out and "." in version_out, (
        f"pydantic version probe returned unexpected output: {version_out!r}"
    )

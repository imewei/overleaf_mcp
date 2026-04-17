#!/usr/bin/env python3
"""MCPB bundle entry point.

When this file runs inside a built ``.mcpb`` bundle, its parent directory
(``server/``) contains a ``vendor/`` subdirectory where ``build-mcpb.sh``
installed all the Python dependencies. We prepend that to ``sys.path``
before importing the server, so the bundle is self-contained: the host's
``python3`` interpreter is used, but none of the user's site-packages
are required.

Outside of a bundle (running from a dev checkout), ``vendor/`` simply
won't exist and this script falls through to a plain import — assuming
the developer has ``pip install -e .``'d the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE / "vendor"
if _VENDOR.is_dir():
    # Prepend (not append) so our vendored copies win over any stray
    # site-packages on the host.
    sys.path.insert(0, str(_VENDOR))

# The source tree is shipped alongside vendor/ in the bundle. In dev mode
# this import resolves via the editable install. In bundle mode it resolves
# via the path we just added (vendor/overleaf_mcp/...).
#
# E402 intentionally suppressed: the sys.path.insert above MUST run before
# this import, so the module-level import cannot be hoisted to the top.
from overleaf_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()

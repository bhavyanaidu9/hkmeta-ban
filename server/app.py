"""
server/app.py — entry point for multi-mode deployment.

Imports the FastAPI application from the root server module so the
openenv validator can find it at the expected path.
"""

import os
import sys

# Ensure the repo root is on the path so root-level modules are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app  # noqa: E402, F401  re-export for uvicorn


def main() -> None:
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=7860, reload=False)


if __name__ == "__main__":
    main()

"""
server/app.py — entry point for multi-mode deployment.

Loads the FastAPI app from the root server.py using importlib to avoid
the naming conflict between the server/ package and server.py module.
"""

import importlib.util
import os

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load root server.py as "root_server" to avoid shadowing this package
_spec = importlib.util.spec_from_file_location(
    "root_server", os.path.join(_root, "server.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

app = _mod.app  # re-export for uvicorn


def main() -> None:
    import uvicorn

    uvicorn.run("server.app:app", host="0.0.0.0", port=7860, reload=False)


if __name__ == "__main__":
    main()

"""
FastAPI server exposing the SQLDebugEnv via the OpenEnv HTTP spec.

Endpoints
---------
POST /reset   — start / restart an episode
POST /step    — submit a fixed query
GET  /state   — inspect current state
GET  /health  — liveness probe
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from environment import SQLDebugEnv, SQLDebugAction

# ---------------------------------------------------------------------------
# Global env instance (sufficient for single-agent evaluation)
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SQL Debug Env",
    description="OpenEnv-compliant SQL Query Debugging environment.",
    version="1.0.0",
)

_env = SQLDebugEnv()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ResetRequest(BaseModel):
    task_name: Optional[str] = None


class StepRequest(BaseModel):
    fixed_query: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reset")
def reset(body: ResetRequest = ResetRequest()) -> dict[str, Any]:
    """
    Reset the environment, optionally specifying a task by name.

    If *task_name* is omitted a task is chosen at random.
    """
    try:
        obs = _env.reset(body.task_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return obs.model_dump()


@app.post("/step")
def step(body: StepRequest) -> dict[str, Any]:
    """
    Submit a fixed SQL query and receive a reward signal.
    """
    if not body.fixed_query.strip():
        raise HTTPException(status_code=400, detail="fixed_query must not be empty.")

    try:
        result = _env.step(SQLDebugAction(fixed_query=body.fixed_query))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "observation": result.observation.model_dump() if result.observation else None,
        "reward": result.reward,
        "done": result.done,
        "info": result.info,
    }


@app.get("/state")
def state() -> dict[str, Any]:
    """Return the current environment state."""
    return _env.state()


# ---------------------------------------------------------------------------
# Entry point (for direct execution: python server.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=7860, reload=False)

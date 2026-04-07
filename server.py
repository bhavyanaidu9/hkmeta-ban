"""
FastAPI server exposing the SQLDebugEnv via the OpenEnv HTTP spec.

Endpoints
---------
POST /reset    — start / restart an episode
POST /step     — submit a fixed query (SELECT only; max 10 000 chars)
GET  /state    — inspect current state
GET  /health   — detailed liveness + readiness probe
GET  /metadata — environment metadata
GET  /schema   — action / observation / state JSON schemas
POST /mcp      — minimal JSON-RPC 2.0 (MCP compliance)
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from environment import SQLDebugEnv, SQLDebugAction
from tasks import TASKS

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
# Security: disallow non-SELECT or write-intent patterns in agent queries.
# Agents must submit plain SELECT statements only.
# ---------------------------------------------------------------------------

# DML / DDL keywords that have no place in a SELECT-only query
_WRITE_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|ATTACH|DETACH)\b",
    re.IGNORECASE,
)

MAX_QUERY_LEN = 10_000  # characters


def _validate_query(query: str) -> Optional[str]:
    """
    Validate a submitted SQL query.

    Returns an error string on failure, or None on success.

    Rules
    -----
    1. Must not be empty after stripping whitespace.
    2. Must not exceed MAX_QUERY_LEN characters.
    3. Must begin with SELECT (environment only supports read queries).
    4. Must not contain write/DDL keywords (belt-and-braces safety check).
    """
    stripped = query.strip()

    if not stripped:
        return "fixed_query must not be empty."

    if len(stripped) > MAX_QUERY_LEN:
        return f"fixed_query exceeds maximum length of {MAX_QUERY_LEN} characters."

    if not stripped.upper().lstrip("(").startswith("SELECT"):
        return (
            "fixed_query must be a SELECT statement. "
            "Write operations (INSERT, UPDATE, DELETE, DROP, …) are not permitted."
        )

    if _WRITE_PATTERN.search(stripped):
        return (
            "fixed_query contains a disallowed write or DDL keyword "
            "(INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, …). "
            "Submit a read-only SELECT query."
        )

    return None


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


@app.get("/")
def root():
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict[str, Any]:
    """
    Detailed liveness + readiness probe.

    Performs a trivial SQLite round-trip to confirm the database layer is
    operational, and reports the number of registered tasks.
    """
    db_ok = False
    db_error: Optional[str] = None
    try:
        con = sqlite3.connect(":memory:")
        con.execute("SELECT 1")
        con.close()
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        db_error = str(exc)

    env_state = _env.state()

    return {
        "status": "healthy" if db_ok else "degraded",
        "version": "1.0.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "database": {
            "backend": "sqlite3 (in-memory)",
            "ok": db_ok,
            "error": db_error,
        },
        "tasks_registered": len(TASKS),
        "env_state": env_state,
    }


@app.get("/metadata")
def metadata() -> dict[str, Any]:
    return {
        "name": "sql-debug-env",
        "description": (
            "SQL Query Debugging environment for AI agents. "
            "Agent receives a buggy SQL query and must fix it."
        ),
        "version": "1.0.0",
        "tags": ["openenv", "sql", "debugging", "real-world"],
        "tasks": [
            {"name": "find_high_earners", "difficulty": "easy"},
            {"name": "top_products_by_category", "difficulty": "medium"},
            {"name": "detect_duplicate_orders", "difficulty": "medium"},
            {"name": "monthly_revenue_trend", "difficulty": "hard"},
            {"name": "slow_query_optimization", "difficulty": "hard"},
        ],
        "reward_range": [0.0, 1.0],
        "max_steps": 5,
    }


@app.get("/schema")
def schema() -> dict[str, Any]:
    return {
        "action": {
            "type": "object",
            "properties": {
                "fixed_query": {
                    "type": "string",
                    "description": "A corrected SQL SELECT query (max 10 000 chars)",
                }
            },
            "required": ["fixed_query"],
        },
        "observation": {
            "type": "object",
            "properties": {
                "task_name": {"type": "string"},
                "buggy_query": {"type": "string"},
                "schema_sql": {"type": "string"},
                "expected_rows": {"type": "array"},
                "task_description": {"type": "string"},
                "attempts_remaining": {"type": "integer"},
                "done": {"type": "boolean"},
            },
        },
        "state": {
            "type": "object",
            "properties": {
                "initialised": {"type": "boolean"},
                "task_name": {"type": "string"},
                "difficulty": {"type": "string"},
                "attempts_remaining": {"type": "integer"},
                "done": {"type": "boolean"},
            },
        },
    }


@app.post("/mcp")
async def mcp(request: dict[str, Any]) -> dict[str, Any]:
    """Minimal JSON-RPC 2.0 endpoint for MCP compliance."""
    method = request.get("method", "")
    req_id = request.get("id", 1)

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sql-debug-env", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {"name": "reset", "description": "Reset the environment"},
                    {"name": "step", "description": "Submit a fixed SQL query"},
                    {"name": "state", "description": "Get current state"},
                ]
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


@app.post("/reset")
def reset(body: ResetRequest = None) -> dict[str, Any]:
    """
    Reset the environment, optionally specifying a task by name.

    Accepts an empty body ``{}``, a body with ``task_name``, or no body at all.
    On success returns the initial observation.
    """
    if body is None:
        body = ResetRequest()
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

    The query must be a non-empty SELECT statement of at most
    ``MAX_QUERY_LEN`` (10 000) characters.  DDL / DML keywords are rejected
    to prevent unintended side-effects.
    """
    err_msg = _validate_query(body.fixed_query)
    if err_msg:
        raise HTTPException(status_code=400, detail=err_msg)

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

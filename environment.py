"""
SQLDebugEnv — OpenEnv-compliant SQL Query Debugging environment.

An AI agent receives a broken SQL query, the database schema, and the
expected output rows. It must submit a corrected query. The environment
runs the query against an in-memory SQLite database seeded with task data
and scores the result 0.0 – 1.0.
"""

from __future__ import annotations

import random
import sqlite3
from typing import Any, Optional

from pydantic import BaseModel, Field

from tasks import TASKS, Task


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SQLDebugObservation(BaseModel):
    task_name: str
    buggy_query: str
    schema_sql: str
    expected_rows: list[dict[str, Any]]
    task_description: str
    attempts_remaining: int
    done: bool = False


class SQLDebugAction(BaseModel):
    fixed_query: str = Field(..., description="The corrected SQL SELECT query.")


class SQLDebugReward(BaseModel):
    reward: float = Field(..., ge=0.0, le=1.0)
    done: bool
    info: dict[str, Any] = Field(default_factory=dict)
    observation: Optional[SQLDebugObservation] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAX_ATTEMPTS = 5


def _run_query(
    schema_sql: str,
    seed_sql: str,
    query: str,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """
    Execute *query* against a fresh in-memory SQLite database that has been
    initialised with *schema_sql* and *seed_sql*.

    Returns ``(rows, error_message)``.  On success ``error_message`` is
    ``None``; on failure ``rows`` is an empty list.
    """
    try:
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.executescript(schema_sql)
        cur.executescript(seed_sql)
        con.commit()
        cur.execute(query)
        rows = [dict(row) for row in cur.fetchall()]
        con.close()
        return rows, None
    except sqlite3.Error as exc:
        return [], str(exc)


def _normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Return a sorted, hashable representation of *rows* for comparison.
    Values are coerced to strings so that int/float mismatches (e.g. 1 vs
    1.0) do not cause false negatives.
    """
    normalised = []
    for row in rows:
        normalised.append(
            {k: str(v) if v is not None else None for k, v in row.items()}
        )
    return sorted(normalised, key=lambda r: str(sorted(r.items())))


def _score(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> float:
    """
    Compute a reward in [0.0, 1.0].

    * 1.0  — exact match (same rows, possibly different order)
    * 0.3–0.8 — partial credit based on row overlap
    * 0.0  — no overlap or wrong columns
    """
    if not expected and not actual:
        return 1.0

    norm_expected = _normalise_rows(expected)
    norm_actual = _normalise_rows(actual)

    # Exact match (order-insensitive)
    if norm_expected == norm_actual:
        return 1.0

    # Column-set check
    expected_cols = set(expected[0].keys()) if expected else set()
    actual_cols = set(actual[0].keys()) if actual else set()
    if expected_cols != actual_cols:
        # Completely wrong schema → 0
        return 0.0

    # Partial credit: Jaccard-style row overlap
    # Convert each row to a frozenset of items for set operations
    def row_key(r: dict) -> frozenset:
        return frozenset((k, str(v) if v is not None else None) for k, v in r.items())

    expected_set = [row_key(r) for r in norm_expected]
    actual_set = [row_key(r) for r in norm_actual]

    # Count how many expected rows appear in actual
    actual_multiset = list(actual_set)
    matches = 0
    for key in expected_set:
        if key in actual_multiset:
            actual_multiset.remove(key)
            matches += 1

    denominator = max(len(expected), len(actual))
    overlap = matches / denominator if denominator > 0 else 0.0

    # Scale overlap to 0.3–0.8 range (wrong = 0.0)
    if overlap == 0.0:
        return 0.0
    return round(0.3 + 0.5 * overlap, 4)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class SQLDebugEnv:
    """
    OpenEnv-compliant environment for SQL query debugging.

    Usage::

        env = SQLDebugEnv()
        obs = env.reset("find_high_earners")
        result = env.step(SQLDebugAction(fixed_query="SELECT ..."))
        print(result.reward)
        env.close()
    """

    def __init__(self) -> None:
        self._task: Optional[Task] = None
        self._expected_rows: list[dict[str, Any]] = []
        self._attempts_remaining: int = MAX_ATTEMPTS
        self._done: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self, task_name: Optional[str] = None) -> SQLDebugObservation:
        """
        Initialise (or re-initialise) the environment for *task_name*.

        If *task_name* is ``None`` a random task is chosen.
        """
        if task_name is None:
            task_name = random.choice(list(TASKS.keys()))

        if task_name not in TASKS:
            raise ValueError(
                f"Unknown task '{task_name}'. Available: {list(TASKS.keys())}"
            )

        self._task = TASKS[task_name]
        self._attempts_remaining = MAX_ATTEMPTS
        self._done = False

        # Pre-compute the expected rows by running the correct query
        self._expected_rows, err = _run_query(
            self._task.schema_sql,
            self._task.seed_sql,
            self._task.correct_query,
        )
        if err:
            raise RuntimeError(
                f"Correct query for task '{task_name}' failed: {err}"
            )

        return self._build_observation()

    def step(self, action: SQLDebugAction) -> SQLDebugReward:
        """
        Execute the agent's *fixed_query* and return a reward.
        """
        if self._task is None:
            raise RuntimeError("Call reset() before step().")
        if self._done:
            return SQLDebugReward(
                reward=0.0,
                done=True,
                info={"error": "Episode already finished. Call reset()."},
                observation=self._build_observation(),
            )

        self._attempts_remaining -= 1

        # Run the submitted query
        actual_rows, sql_error = _run_query(
            self._task.schema_sql,
            self._task.seed_sql,
            action.fixed_query,
        )

        if sql_error:
            reward = 0.0
            info: dict[str, Any] = {"error": sql_error, "sql_error": True}
        else:
            reward = _score(self._expected_rows, actual_rows)
            info = {
                "error": None,
                "expected_row_count": len(self._expected_rows),
                "actual_row_count": len(actual_rows),
            }

        # Episode ends on exact match or when attempts are exhausted
        if reward == 1.0 or self._attempts_remaining == 0:
            self._done = True

        info["attempts_remaining"] = self._attempts_remaining

        return SQLDebugReward(
            reward=reward,
            done=self._done,
            info=info,
            observation=self._build_observation(),
        )

    def state(self) -> dict[str, Any]:
        """Return current environment state as a plain dict."""
        if self._task is None:
            return {"initialised": False}
        return {
            "initialised": True,
            "task_name": self._task.task_name,
            "difficulty": self._task.difficulty,
            "attempts_remaining": self._attempts_remaining,
            "done": self._done,
        }

    def close(self) -> None:
        """Release resources (no-op for in-memory SQLite)."""
        self._task = None
        self._expected_rows = []
        self._attempts_remaining = MAX_ATTEMPTS
        self._done = False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_observation(self) -> SQLDebugObservation:
        assert self._task is not None
        return SQLDebugObservation(
            task_name=self._task.task_name,
            buggy_query=self._task.buggy_query,
            schema_sql=self._task.schema_sql,
            expected_rows=self._expected_rows,
            task_description=self._task.task_description,
            attempts_remaining=self._attempts_remaining,
            done=self._done,
        )


__all__ = [
    "SQLDebugEnv",
    "SQLDebugObservation",
    "SQLDebugAction",
    "SQLDebugReward",
]

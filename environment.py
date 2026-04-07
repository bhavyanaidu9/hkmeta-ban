"""
SQLDebugEnv — OpenEnv-compliant SQL Query Debugging environment.

An AI agent receives a broken SQL query, the database schema, and the
expected output rows. It must submit a corrected query. The environment
runs the query against an in-memory SQLite database seeded with task data
and scores the result 0.0 – 1.0.

Grading formula
---------------
Given E = set of expected rows, A = set of actual rows (after column check):

    score = 1.0                        if E == A  (exact, order-insensitive)
    score = 0.0                        if columns differ, or A is empty and E is not
    score = 0.3 + 0.5 * (|E∩A| / max(|E|,|A|))   otherwise  (Jaccard-style partial credit)

The 0.3 base ensures that any meaningful partial overlap produces a non-trivial
signal, while the 0.5 multiplier caps partial credit at 0.8 — reserving 1.0
exclusively for exact matches. This prevents agents from gaming the scorer by
returning a single correct row.
"""

from __future__ import annotations

import random
import sqlite3
import time
import threading
from typing import Any, Optional

from pydantic import BaseModel, Field

from tasks import TASKS, Task

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SQLDebugObservation(BaseModel):
    """Observation returned by reset() and included in every step() result."""

    task_name: str
    buggy_query: str
    schema_sql: str
    expected_rows: list[dict[str, Any]]
    task_description: str
    attempts_remaining: int
    done: bool = False


class SQLDebugAction(BaseModel):
    """Action submitted by the agent."""

    fixed_query: str = Field(..., description="The corrected SQL SELECT query.")


class SQLDebugReward(BaseModel):
    """Result returned by step()."""

    reward: float = Field(..., ge=0.0, le=1.0)
    done: bool
    info: dict[str, Any] = Field(default_factory=dict)
    observation: Optional[SQLDebugObservation] = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ATTEMPTS = 5
QUERY_TIMEOUT_SECONDS = 5.0  # wall-clock limit per submitted query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_query(
    schema_sql: str,
    seed_sql: str,
    query: str,
    timeout: float = QUERY_TIMEOUT_SECONDS,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """
    Execute *query* against a fresh in-memory SQLite database initialised
    with *schema_sql* and *seed_sql*.

    A progress handler interrupts the query if it exceeds *timeout* seconds,
    protecting against accidental or adversarial long-running queries (e.g.
    a Cartesian product with no WHERE clause on a large seed table).

    Returns ``(rows, error_message)``.  On success ``error_message`` is
    ``None``; on failure ``rows`` is an empty list.
    """
    result: list = [[], None]
    exception_holder: list = [None]

    def _execute() -> None:
        try:
            con = sqlite3.connect(":memory:")
            con.row_factory = sqlite3.Row

            deadline = time.monotonic() + timeout

            def _progress() -> int:
                """Return non-zero to interrupt the current SQL statement."""
                return 1 if time.monotonic() > deadline else 0

            # Check every ~100 SQLite virtual-machine opcodes
            con.set_progress_handler(_progress, 100)

            cur = con.cursor()
            cur.executescript(schema_sql)
            cur.executescript(seed_sql)
            con.commit()
            cur.execute(query)
            result[0] = [dict(row) for row in cur.fetchall()]
            con.close()
        except sqlite3.OperationalError as exc:
            msg = str(exc)
            if "interrupted" in msg.lower():
                result[1] = f"Query timed out after {timeout:.0f} seconds"
            else:
                result[1] = msg
        except sqlite3.Error as exc:
            result[1] = str(exc)
        except Exception as exc:  # noqa: BLE001
            exception_holder[0] = exc

    thread = threading.Thread(target=_execute, daemon=True)
    thread.start()
    thread.join(timeout + 1.0)  # give the progress handler time to fire

    if thread.is_alive():
        # Should not happen in practice due to progress handler, but belt-and-braces
        return [], f"Query timed out after {timeout:.0f} seconds"

    if exception_holder[0] is not None:
        return [], f"Unexpected error: {exception_holder[0]}"

    return result[0], result[1]


def _normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Return a sorted, comparable representation of *rows*.

    Values are coerced to strings so that int/float mismatches (e.g. 1 vs 1.0)
    do not cause false negatives.  Rows are then sorted deterministically so
    that order-insensitive comparison works with a simple ``==`` check.
    """
    normalised = [
        {k: str(v) if v is not None else None for k, v in row.items()} for row in rows
    ]
    return sorted(normalised, key=lambda r: str(sorted(r.items())))


def _score(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    """
    Compute a reward in [0.0, 1.0] and a metrics dict.

    Scoring tiers
    ~~~~~~~~~~~~~
    * **1.0** — exact match (same rows, possibly different order).
    * **0.3 – 0.8** — partial credit: correct columns, some row overlap.
      Formula: ``0.3 + 0.5 * jaccard`` where
      ``jaccard = |E ∩ A| / max(|E|, |A|)``.
    * **0.0** — wrong columns, SQL error, or zero row overlap.

    Design rationale
    ~~~~~~~~~~~~~~~~
    The 0.3 floor rewards queries that are structurally correct (right columns,
    right table) even when filtering is off.  The 0.5 multiplier means an agent
    can earn at most 0.8 from partial credit — preserving 1.0 as a meaningful
    "solved" signal.  Jaccard over ``max(|E|, |A|)`` penalises both missing
    rows *and* spurious extra rows, making it hard to game by returning a
    superset of the expected output.

    Returns
    -------
    (reward, metrics)  where metrics contains: match_type, jaccard_similarity,
    row_overlap, expected_row_count, actual_row_count.
    """
    metrics: dict[str, Any] = {
        "expected_row_count": len(expected),
        "actual_row_count": len(actual),
        "jaccard_similarity": 0.0,
        "row_overlap": 0,
        "match_type": "none",
    }

    if not expected and not actual:
        metrics["match_type"] = "exact"
        metrics["jaccard_similarity"] = 1.0
        return 1.0, metrics

    norm_expected = _normalise_rows(expected)
    norm_actual = _normalise_rows(actual)

    # Exact match (order-insensitive)
    if norm_expected == norm_actual:
        metrics["match_type"] = "exact"
        metrics["jaccard_similarity"] = 1.0
        metrics["row_overlap"] = len(expected)
        return 1.0, metrics

    # Column-set check — completely wrong schema → 0
    expected_cols = set(expected[0].keys()) if expected else set()
    actual_cols = set(actual[0].keys()) if actual else set()
    if expected_cols != actual_cols:
        metrics["match_type"] = "wrong_columns"
        return 0.0, metrics

    # Partial credit: Jaccard-style row overlap
    def _row_key(r: dict) -> frozenset:
        return frozenset((k, str(v) if v is not None else None) for k, v in r.items())

    expected_keys = [_row_key(r) for r in norm_expected]
    actual_keys = list(_row_key(r) for r in norm_actual)

    matches = 0
    remaining = list(actual_keys)
    for key in expected_keys:
        if key in remaining:
            remaining.remove(key)
            matches += 1

    denominator = max(len(expected), len(actual))
    jaccard = matches / denominator if denominator > 0 else 0.0

    metrics["row_overlap"] = matches
    metrics["jaccard_similarity"] = round(jaccard, 4)

    if jaccard == 0.0:
        metrics["match_type"] = "no_overlap"
        return 0.0, metrics

    metrics["match_type"] = "partial"
    reward = round(0.3 + 0.5 * jaccard, 4)
    return reward, metrics


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class SQLDebugEnv:
    """
    OpenEnv-compliant environment for SQL query debugging.

    The agent receives a buggy SQL query plus schema + seed data, and must
    submit a corrected query via ``step()``.  Each episode allows up to
    ``MAX_ATTEMPTS`` (5) attempts; the episode ends early on a perfect score.

    Usage::

        env = SQLDebugEnv()
        obs = env.reset("find_high_earners")
        result = env.step(SQLDebugAction(fixed_query="SELECT ..."))
        print(result.reward)   # float in [0.0, 1.0]
        env.close()

    Thread safety
    ~~~~~~~~~~~~~
    Each ``SQLDebugEnv`` instance maintains its own in-memory state.  For
    concurrent evaluation, create one instance per request (or use the
    stateless HTTP endpoints in ``server.py``).
    """

    def __init__(self) -> None:
        self._task: Optional[Task] = None
        self._expected_rows: list[dict[str, Any]] = []
        self._attempts_remaining: int = MAX_ATTEMPTS
        self._done: bool = False
        self._start_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self, task_name: Optional[str] = None) -> SQLDebugObservation:
        """
        Initialise (or re-initialise) the environment for *task_name*.

        Parameters
        ----------
        task_name:
            Name of the task to run.  Pass ``None`` to select one at random.

        Raises
        ------
        ValueError
            If *task_name* is not in the task registry.
        RuntimeError
            If the reference (correct) query fails to execute — this indicates
            a bug in the task definition itself.
        """
        if task_name is None:
            task_name = random.choice(list(TASKS.keys()))

        if task_name not in TASKS:
            raise ValueError(
                f"Unknown task '{task_name}'. "
                f"Available tasks: {sorted(TASKS.keys())}"
            )

        self._task = TASKS[task_name]
        self._attempts_remaining = MAX_ATTEMPTS
        self._done = False
        self._start_time = time.monotonic()

        # Pre-compute the ground-truth rows once per episode
        self._expected_rows, err = _run_query(
            self._task.schema_sql,
            self._task.seed_sql,
            self._task.correct_query,
        )
        if err:
            raise RuntimeError(f"Reference query for task '{task_name}' failed: {err}")

        return self._build_observation()

    def step(self, action: SQLDebugAction) -> SQLDebugReward:
        """
        Execute the agent's *fixed_query* and return a reward signal.

        Parameters
        ----------
        action:
            ``SQLDebugAction`` containing the agent's corrected SQL query.

        Returns
        -------
        SQLDebugReward
            Contains ``reward`` (float), ``done`` (bool), ``info`` (dict with
            detailed metrics), and the next ``observation``.

        Raises
        ------
        RuntimeError
            If ``reset()`` has not been called first.
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

        # Time the query execution
        t0 = time.monotonic()
        actual_rows, sql_error = _run_query(
            self._task.schema_sql,
            self._task.seed_sql,
            action.fixed_query,
        )
        execution_ms = round((time.monotonic() - t0) * 1000, 1)

        if sql_error:
            reward = 0.0
            info: dict[str, Any] = {
                "error": sql_error,
                "sql_error": True,
                "execution_time_ms": execution_ms,
                "match_type": "error",
                "jaccard_similarity": 0.0,
                "row_overlap": 0,
                "expected_row_count": len(self._expected_rows),
                "actual_row_count": 0,
            }
        else:
            reward, score_metrics = _score(self._expected_rows, actual_rows)
            info = {
                "error": None,
                "sql_error": False,
                "execution_time_ms": execution_ms,
                **score_metrics,
            }

        # Episode ends on exact match or exhausted attempts
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
        """Return the current environment state as a plain dict."""
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
        """Release resources (no-op for in-memory SQLite, resets state)."""
        self._task = None
        self._expected_rows = []
        self._attempts_remaining = MAX_ATTEMPTS
        self._done = False
        self._start_time = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_observation(self) -> SQLDebugObservation:
        assert self._task is not None, "Task must be set before building observation"
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

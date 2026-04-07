"""
Baseline inference script for the SQL Query Debugging environment.

Runs all five tasks sequentially using an LLM (default: Qwen/Qwen2.5-72B-Instruct
via the HuggingFace router) and emits results in the OpenEnv stdout format.

Required environment variables
--------------------------------
HF_TOKEN      — HuggingFace API token (used as the OpenAI-compatible API key)

Optional environment variables
--------------------------------
API_BASE_URL  — defaults to "https://router.huggingface.co/v1"
MODEL_NAME    — defaults to "Qwen/Qwen2.5-72B-Instruct"
ENV_URL       — defaults to "https://nallgopu-sql-debug-env.hf.space"
LOG_FILE      — path to a log file for diagnostic output (default: inference.log)

Stdout format (OpenEnv spec)
-----------------------------
[START] task=<task_name> env=sql-debug-env model=<model_name>
[STEP] step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>
[END] success=<true|false> steps=<n> score=<0.000> rewards=<r1,r2,...,rn>
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import List, Optional

import httpx
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL: str = os.environ.get("API_BASE_URL", "https://router.huggingface.co/v1")
MODEL_NAME: str = os.environ.get("MODEL_NAME", "Qwen/Qwen2.5-72B-Instruct")
HF_TOKEN: str = os.environ.get("HF_TOKEN", "")
ENV_URL: str = os.environ.get("ENV_URL", "https://nallgopu-sql-debug-env.hf.space")
LOG_FILE: str = os.environ.get("LOG_FILE", "inference.log")

TASK_NAMES: list[str] = [
    "find_high_earners",
    "top_products_by_category",
    "detect_duplicate_orders",
    "monthly_revenue_trend",
    "slow_query_optimization",
]

ENV_NAME = "sql-debug-env"
MAX_STEPS = 5
SUCCESS_SCORE_THRESHOLD = 0.5

# ---------------------------------------------------------------------------
# Logging setup
#
# Diagnostic messages (warnings, errors, debug info) go to a rotating log
# file AND to stderr so they don't pollute the OpenEnv stdout format.
# The [START] / [STEP] / [END] lines are written directly to stdout via
# the `_openenv_print` helper to guarantee exact format compliance.
# ---------------------------------------------------------------------------

_logger = logging.getLogger("inference")
_logger.setLevel(logging.DEBUG)

# File handler — full diagnostics
_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
)
_logger.addHandler(_file_handler)

# Stderr handler — warnings and above only
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
_logger.addHandler(_stderr_handler)


def _openenv_print(msg: str) -> None:
    """Write an OpenEnv-format line to stdout with immediate flush."""
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a SQL debugging assistant. "
    "You will receive a buggy SQL query, the database schema, and the expected output. "
    "Fix the query. Respond with ONLY the corrected SQL query, nothing else. "
    "Do not include any explanation, markdown fences, or commentary — "
    "just the raw SQL statement."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_user_message(obs: dict) -> str:
    """Construct the LLM prompt from an observation dict."""
    expected = obs.get("expected_rows", [])
    preview = expected[:5]
    more = len(expected) - len(preview)
    preview_str = "\n".join(str(r) for r in preview)
    if more > 0:
        preview_str += f"\n... ({more} more rows)"

    return (
        f"Task: {obs.get('task_description', '')}\n\n"
        f"Database schema:\n{obs.get('schema_sql', '')}\n\n"
        f"Buggy query:\n{obs.get('buggy_query', '')}\n\n"
        f"Expected output ({len(expected)} rows — first 5 shown):\n"
        f"{preview_str}\n\n"
        f"Attempts remaining: {obs.get('attempts_remaining', 0)}\n\n"
        "Return ONLY the fixed SQL query."
    )


_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str) -> str:
    """Strip markdown code fences if the model wraps its response."""
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _log_action(sql: str) -> str:
    """Collapse a multi-line SQL query to a single line for log output."""
    return sql.replace("\n", " ").replace("\r", "").strip()


# ---------------------------------------------------------------------------
# OpenEnv stdout helpers — exact format required by the spec
# ---------------------------------------------------------------------------


def log_start(task: str, env: str, model: str) -> None:
    _openenv_print(f"[START] task={task} env={env} model={model}")
    _logger.info("Episode started: task=%s model=%s", task, model)


def log_step(
    step: int,
    action: str,
    reward: float,
    done: bool,
    error: Optional[str],
) -> None:
    error_val = error if error else "null"
    done_val = str(done).lower()
    _openenv_print(
        f"[STEP] step={step} action={_log_action(action)} "
        f"reward={reward:.2f} done={done_val} error={error_val}"
    )
    _logger.debug(
        "Step %d: reward=%.2f done=%s error=%s",
        step, reward, done_val, error_val,
    )


def log_end(
    success: bool,
    steps: int,
    score: float,
    rewards: List[float],
) -> None:
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    _openenv_print(
        f"[END] success={str(success).lower()} steps={steps} "
        f"score={score:.3f} rewards={rewards_str}"
    )
    _logger.info(
        "Episode ended: success=%s steps=%d score=%.3f",
        success, steps, score,
    )


# ---------------------------------------------------------------------------
# Run a single task episode
# ---------------------------------------------------------------------------


def run_task(task_name: str, client: OpenAI, http: httpx.Client) -> None:
    """
    Run one full episode for *task_name*.

    Parameters
    ----------
    task_name:
        Name of the task to run (must match a registered task).
    client:
        Configured OpenAI-compatible client for LLM inference.
    http:
        Configured httpx client pointing at the deployed environment.
    """
    _logger.info("Resetting environment for task: %s", task_name)
    r = http.post("/reset", json={"task_name": task_name})
    if r.status_code != 200:
        _logger.error(
            "/reset failed for task %s: %s %s", task_name, r.status_code, r.text
        )
        return

    obs: dict = r.json()
    log_start(task=task_name, env=ENV_NAME, model=MODEL_NAME)

    step_num = 0
    rewards: List[float] = []
    final_done = False
    conversation: list[dict] = []

    while step_num < MAX_STEPS and not final_done:
        step_num += 1

        user_msg = _build_user_message(obs)
        conversation.append({"role": "user", "content": user_msg})

        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversation,
                temperature=0.0,
                max_tokens=512,
            )
            raw_answer = response.choices[0].message.content or ""
        except Exception as exc:
            raw_answer = obs.get("buggy_query", "SELECT 1")
            _logger.warning("Model API error on step %d: %s", step_num, exc)

        sql_answer = _extract_sql(raw_answer)
        conversation.append({"role": "assistant", "content": sql_answer})

        step_r = http.post("/step", json={"fixed_query": sql_answer})
        if step_r.status_code != 200:
            _logger.error("/step failed: %s %s", step_r.status_code, step_r.text)
            break

        result: dict = step_r.json()
        reward = float(result.get("reward", 0.0))
        final_done = result.get("done", False)

        error_val = result.get("info", {}).get("error") or None
        if isinstance(error_val, str) and error_val.lower() == "none":
            error_val = None

        rewards.append(reward)

        log_step(
            step=step_num,
            action=sql_answer,
            reward=reward,
            done=final_done,
            error=error_val,
        )

        if final_done:
            break

        next_obs = result.get("observation")
        if next_obs:
            obs = next_obs

    score = sum(rewards) / len(rewards) if rewards else 0.0
    score = min(max(score, 0.0), 1.0)
    success = score >= SUCCESS_SCORE_THRESHOLD

    log_end(success=success, steps=step_num, score=score, rewards=rewards)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run baseline agent across all registered tasks."""
    if not HF_TOKEN:
        _logger.critical("HF_TOKEN environment variable is not set.")
        sys.exit(1)

    _logger.info(
        "Starting inference: model=%s env=%s tasks=%s",
        MODEL_NAME, ENV_URL, TASK_NAMES,
    )

    client = OpenAI(api_key=HF_TOKEN, base_url=API_BASE_URL)

    with httpx.Client(base_url=ENV_URL, timeout=60.0) as http:
        for task_name in TASK_NAMES:
            run_task(task_name, client, http)

    _logger.info("All tasks complete.")


if __name__ == "__main__":
    main()

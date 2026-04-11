"""
inference.py — Baseline inference script for the SQL Query Debugging environment.

Runs all five SQL debugging tasks sequentially using an LLM via an
OpenAI-compatible API and emits results in the required OpenEnv stdout format.

Required environment variables
--------------------------------
HF_TOKEN      — Hugging Face API token (mandatory, no default)

Optional environment variables (with defaults)
------------------------------------------------
API_BASE_URL  — API endpoint for the LLM
                default: "https://router.huggingface.co/v1"
MODEL_NAME    — Model identifier used for inference
                default: "Qwen/Qwen2.5-72B-Instruct"
ENV_URL       — Base URL of the deployed SQL Debug Env HF Space
                default: "https://nallgopu-sql-debug-env.hf.space"

Stdout format (OpenEnv spec — exact)
--------------------------------------
[START] task=<task_name> env=<benchmark> model=<model_name>
[STEP] step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>
[END] success=<true|false> steps=<n> score=<0.0000> rewards=<r1,r2,...,rn>
"""

from __future__ import annotations

import os
import re
import sys
from typing import List, Optional

import httpx
from openai import OpenAI

# ---------------------------------------------------------------------------
# Read environment variables with defaults where required
# ---------------------------------------------------------------------------

API_BASE_URL: str = os.getenv("API_BASE_URL", "https://router.huggingface.co/v1")
MODEL_NAME: str = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-72B-Instruct")
HF_TOKEN: Optional[str] = os.getenv("HF_TOKEN")
ENV_URL: str = os.getenv("ENV_URL", "https://nallgopu-sql-debug-env.hf.space")

if HF_TOKEN is None:
    raise ValueError("HF_TOKEN environment variable is required")

# Initialize OpenAI client
client = OpenAI(
    base_url=API_BASE_URL,
    api_key=HF_TOKEN,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENV_NAME = "sql-debug-env"
MAX_STEPS = 5
SUCCESS_THRESHOLD = 0.99  # reward >= 0.99 counts as solved (exact match)

TASK_NAMES: list[str] = [
    "find_high_earners",
    "top_products_by_category",
    "detect_duplicate_orders",
    "monthly_revenue_trend",
    "slow_query_optimization",
]

SYSTEM_PROMPT = (
    "You are a SQL debugging assistant. "
    "You will receive a buggy SQL query, the database schema, and the expected output. "
    "Fix the query. Respond with ONLY the corrected SQL query, nothing else. "
    "Do not include any explanation, markdown fences, or commentary — "
    "just the raw SQL statement."
)

# ---------------------------------------------------------------------------
# Output helpers — strict OpenEnv format (stdout only)
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str) -> str:
    """Strip markdown code fences if the model wraps its response."""
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else text.strip()


def _inline(text: str) -> str:
    """Collapse multi-line text to a single line for log fields."""
    return text.replace("\n", " ").replace("\r", "").strip()


def log_start(task: str, env: str, model: str) -> None:
    print(f"[START] task={task} env={env} model={model}", flush=True)


def log_step(
    step: int,
    action: str,
    reward: float,
    done: bool,
    error: Optional[str],
) -> None:
    error_val = error if error else "null"
    done_val = str(done).lower()
    print(
        f"[STEP] step={step} action={_inline(action)} "
        f"reward={reward:.2f} done={done_val} error={error_val}",
        flush=True,
    )


def log_end(success: bool, steps: int, rewards: List[float]) -> None:
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    # Task score = best reward achieved, clamped strictly to (0, 1)
    score = max(rewards) if rewards else 0.01
    score = max(0.01, min(0.99, score))
    print(
        f"[END] success={str(success).lower()} steps={steps} score={score:.4f} rewards={rewards_str}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Prompt builder
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


# ---------------------------------------------------------------------------
# Single task episode
# ---------------------------------------------------------------------------


def run_task(task_name: str, http: httpx.Client) -> None:
    """Run one full episode for *task_name* and emit OpenEnv stdout lines."""
    # Reset the environment
    try:
        r = http.post("/reset", json={"task_name": task_name})
        r.raise_for_status()
        obs: dict = r.json()
    except Exception as exc:
        # Emit minimal lines so the harness always sees START + END
        log_start(task=task_name, env=ENV_NAME, model=MODEL_NAME)
        log_end(success=False, steps=0, rewards=[0.01])
        print(f"WARNING: /reset failed for {task_name}: {exc}", file=sys.stderr)
        return

    log_start(task=task_name, env=ENV_NAME, model=MODEL_NAME)

    step_num = 0
    rewards: List[float] = []
    final_done = False
    conversation: list[dict] = []

    try:
        while step_num < MAX_STEPS and not final_done:
            step_num += 1

            # Build prompt and call the LLM
            user_msg = _build_user_message(obs)
            conversation.append({"role": "user", "content": user_msg})

            error_str: Optional[str] = None
            try:
                response = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}]
                    + conversation,
                    temperature=0.0,
                    max_tokens=512,
                )
                raw_answer = response.choices[0].message.content or ""
            except Exception as exc:
                raw_answer = obs.get("buggy_query", "SELECT 1")
                error_str = str(exc)

            sql_answer = _extract_sql(raw_answer)
            conversation.append({"role": "assistant", "content": sql_answer})

            # Submit to the environment
            try:
                step_r = http.post("/step", json={"fixed_query": sql_answer})
                step_r.raise_for_status()
                result: dict = step_r.json()
            except Exception as exc:
                log_step(
                    step=step_num,
                    action=sql_answer,
                    reward=0.01,
                    done=True,
                    error=str(exc),
                )
                rewards.append(0.01)
                final_done = True
                break

            reward = float(result.get("reward", 0.01))
            final_done = bool(result.get("done", False))

            # Extract any SQL error from the info dict
            info_error = result.get("info", {}).get("error") or None
            if isinstance(info_error, str) and info_error.lower() in ("none", ""):
                info_error = None
            step_error = error_str or info_error

            rewards.append(reward)
            log_step(
                step=step_num,
                action=sql_answer,
                reward=reward,
                done=final_done,
                error=step_error,
            )

            if final_done:
                break

            next_obs = result.get("observation")
            if next_obs:
                obs = next_obs

    except Exception as exc:
        print(f"WARNING: Unhandled error in task {task_name}: {exc}", file=sys.stderr)
        final_done = True

    # Episode success = any exact match (reward >= 0.99)
    success = any(r >= SUCCESS_THRESHOLD for r in rewards)
    log_end(success=success, steps=step_num, rewards=rewards)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the baseline agent across all registered tasks."""
    with httpx.Client(base_url=ENV_URL, timeout=60.0) as http:
        for task_name in TASK_NAMES:
            run_task(task_name, http)


if __name__ == "__main__":
    main()

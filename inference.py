"""
Baseline inference script for the SQL Query Debugging environment.

Runs all three tasks sequentially using an LLM (default: Qwen/Qwen2.5-72B-Instruct
via the HuggingFace router) and logs results to stdout in the OpenEnv format.

Required environment variables
--------------------------------
HF_TOKEN      — HuggingFace API token (used as the OpenAI-compatible API key)

Optional environment variables
--------------------------------
API_BASE_URL  — defaults to "https://router.huggingface.co/v1"
MODEL_NAME    — defaults to "Qwen/Qwen2.5-72B-Instruct"
ENV_URL       — defaults to "https://nallgopu-sql-debug-env.hf.space"

Stdout format (OpenEnv spec)
-----------------------------
[START] task=<task_name> env=sql-debug-env model=<model_name>
[STEP] step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>
[END] success=<true|false> steps=<n> score=<0.000> rewards=<r1,r2,...,rn>
"""

from __future__ import annotations

import os
import re
import sys
from typing import List, Optional

import httpx
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL: str = os.environ.get(
    "API_BASE_URL", "https://router.huggingface.co/v1"
)
MODEL_NAME: str = os.environ.get(
    "MODEL_NAME", "Qwen/Qwen2.5-72B-Instruct"
)
HF_TOKEN: str = os.environ.get("HF_TOKEN", "")

# ✅ FIX 1: Talk to the deployed HF Space via HTTP, not local import
ENV_URL: str = os.environ.get(
    "ENV_URL", "https://nallgopu-sql-debug-env.hf.space"
)

TASK_NAMES: list[str] = [
    "find_high_earners",
    "top_products_by_category",
    "monthly_revenue_trend",
]

ENV_NAME = "sql-debug-env"
MAX_STEPS = 5
SUCCESS_SCORE_THRESHOLD = 0.5

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
# Helper: build user message from observation dict
# ---------------------------------------------------------------------------


def _build_user_message(obs: dict) -> str:
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
# Helper: strip markdown fences if model adds them
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Helper: collapse SQL to single line for log output
# ---------------------------------------------------------------------------


def _log_action(sql: str) -> str:
    return sql.replace("\n", " ").replace("\r", "").strip()


# ---------------------------------------------------------------------------
# Logging helpers — exact OpenEnv stdout format
# ---------------------------------------------------------------------------


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
    # ✅ FIX 2: removed !r so action is a plain string, not repr-quoted
    print(
        f"[STEP] step={step} action={_log_action(action)} "
        f"reward={reward:.2f} done={done_val} error={error_val}",
        flush=True,
    )


def log_end(
    success: bool,
    steps: int,
    score: float,
    rewards: List[float],
) -> None:
    # ✅ FIX 3: rewards formatted to exactly 2 decimal places (was 3)
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    print(
        f"[END] success={str(success).lower()} steps={steps} "
        f"score={score:.3f} rewards={rewards_str}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Run a single task episode
# ---------------------------------------------------------------------------


def run_task(task_name: str, client: OpenAI, http: httpx.Client) -> None:
    # ✅ FIX 1 (continued): reset via HTTP POST, not local env.reset()
    r = http.post("/reset", json={"task_name": task_name})
    if r.status_code != 200:
        print(
            f"[ERROR] /reset failed for task {task_name}: {r.status_code} {r.text}",
            file=sys.stderr,
            flush=True,
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

        # Build prompt from HTTP observation dict
        user_msg = _build_user_message(obs)
        conversation.append({"role": "user", "content": user_msg})

        # Call the LLM via OpenAI-compatible client
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
            print(
                f"[WARN] Model API error on step {step_num}: {exc}",
                file=sys.stderr,
                flush=True,
            )

        sql_answer = _extract_sql(raw_answer)
        conversation.append({"role": "assistant", "content": sql_answer})

        # ✅ FIX 1 (continued): submit action via HTTP POST /step
        step_r = http.post("/step", json={"fixed_query": sql_answer})
        if step_r.status_code != 200:
            print(
                f"[ERROR] /step failed: {step_r.status_code} {step_r.text}",
                file=sys.stderr,
                flush=True,
            )
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

    # Compute normalized score: average across all steps taken
    score = sum(rewards) / len(rewards) if rewards else 0.0
    score = min(max(score, 0.0), 1.0)
    success = score >= SUCCESS_SCORE_THRESHOLD

    log_end(success=success, steps=step_num, score=score, rewards=rewards)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if not HF_TOKEN:
        print(
            "[ERROR] HF_TOKEN environment variable is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = OpenAI(api_key=HF_TOKEN, base_url=API_BASE_URL)

    # ✅ FIX 1: use httpx to call the Space over HTTP
    with httpx.Client(base_url=ENV_URL, timeout=60.0) as http:
        for task_name in TASK_NAMES:
            run_task(task_name, client, http)


if __name__ == "__main__":
    main()
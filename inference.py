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

Stdout format (OpenEnv spec)
-----------------------------
[START] task=<task_name> env=sql-debug-env model=<model_name>
[STEP]  step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>
[END]   success=<true|false> steps=<n> score=<0.000> rewards=<r1,r2,...,rn>
"""

from __future__ import annotations

import os
import re
import sys

from openai import OpenAI

from environment import SQLDebugEnv, SQLDebugAction, SQLDebugObservation

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

TASK_NAMES: list[str] = [
    "find_high_earners",
    "top_products_by_category",
    "monthly_revenue_trend",
]

ENV_NAME = "sql-debug-env"
MAX_STEPS = 5

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
# Helper: build user message
# ---------------------------------------------------------------------------


def _build_user_message(obs: SQLDebugObservation) -> str:
    expected_preview = obs.expected_rows[:5]
    more = len(obs.expected_rows) - len(expected_preview)
    preview_str = "\n".join(str(r) for r in expected_preview)
    if more > 0:
        preview_str += f"\n... ({more} more rows)"

    return (
        f"Task: {obs.task_description}\n\n"
        f"Database schema:\n{obs.schema_sql}\n\n"
        f"Buggy query:\n{obs.buggy_query}\n\n"
        f"Expected output ({len(obs.expected_rows)} rows — first 5 shown):\n"
        f"{preview_str}\n\n"
        f"Attempts remaining: {obs.attempts_remaining}\n\n"
        "Return ONLY the fixed SQL query."
    )


# ---------------------------------------------------------------------------
# Helper: extract SQL from model response
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str) -> str:
    """Strip markdown fences if the model adds them despite the instruction."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Helper: escape action string for single-line log output
# ---------------------------------------------------------------------------


def _log_action(sql: str) -> str:
    return sql.replace("\n", " ").replace("\r", "")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_task(client: OpenAI, env: SQLDebugEnv, task_name: str) -> None:
    obs = env.reset(task_name)

    print(
        f"[START] task={task_name} env={ENV_NAME} model={MODEL_NAME}",
        flush=True,
    )

    step_num = 0
    rewards: list[float] = []
    final_done = False
    conversation: list[dict] = []

    while step_num < MAX_STEPS and not final_done:
        step_num += 1

        # Build prompt
        user_msg = _build_user_message(obs)
        conversation.append({"role": "user", "content": user_msg})

        # Call the model
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}]
                + conversation,
                temperature=0.0,
                max_tokens=512,
            )
            raw_answer = response.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            raw_answer = obs.buggy_query  # fall back to buggy query on API error
            print(
                f"[WARN] Model API error on step {step_num}: {exc}",
                file=sys.stderr,
                flush=True,
            )

        sql_answer = _extract_sql(raw_answer)
        conversation.append({"role": "assistant", "content": sql_answer})

        # Submit to env
        result = env.step(SQLDebugAction(fixed_query=sql_answer))
        rewards.append(result.reward)
        final_done = result.done

        error_str = result.info.get("error") or "null"
        if error_str == "null" or not error_str:
            error_str = "null"

        print(
            f"[STEP] step={step_num} "
            f"action={_log_action(sql_answer)!r} "
            f"reward={result.reward:.2f} "
            f"done={'true' if result.done else 'false'} "
            f"error={error_str}",
            flush=True,
        )

        if result.observation:
            obs = result.observation

    success = any(r == 1.0 for r in rewards)
    score = max(rewards) if rewards else 0.0
    rewards_str = ",".join(f"{r:.3f}" for r in rewards)

    print(
        f"[END] success={'true' if success else 'false'} "
        f"steps={step_num} "
        f"score={score:.3f} "
        f"rewards={rewards_str}",
        flush=True,
    )


def main() -> None:
    if not HF_TOKEN:
        print(
            "[ERROR] HF_TOKEN environment variable is not set.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = OpenAI(api_key=HF_TOKEN, base_url=API_BASE_URL)
    env = SQLDebugEnv()

    for task_name in TASK_NAMES:
        run_task(client, env, task_name)

    env.close()


if __name__ == "__main__":
    main()

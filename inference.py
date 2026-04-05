"""
Baseline inference script for the SQL Query Debugging environment.

Calls the deployed HF Space via HTTP endpoints (POST /reset, POST /step).
Logs results to stdout in the strict OpenEnv format.

Required environment variables:
    HF_TOKEN     — HuggingFace API token
    API_BASE_URL — LLM API endpoint (default: https://router.huggingface.co/v1)
    MODEL_NAME   — Model identifier (default: Qwen/Qwen2.5-72B-Instruct)
    PING_URL     — HF Space base URL (default: http://localhost:7860)
"""

from __future__ import annotations

import os
import re
import sys

import requests
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE_URL: str = os.environ.get("API_BASE_URL", "https://router.huggingface.co/v1")
MODEL_NAME: str = os.environ.get("MODEL_NAME", "Qwen/Qwen2.5-72B-Instruct")
HF_TOKEN: str = os.environ.get("HF_TOKEN", "")
PING_URL: str = os.environ.get("PING_URL", "http://localhost:7860").rstrip("/")

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
# Helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _log_action(sql: str) -> str:
    """Collapse SQL to single line for log output."""
    return sql.replace("\n", " ").replace("\r", "").strip()


def _build_user_message(obs: dict) -> str:
    expected_rows = obs.get("expected_rows", [])
    preview = expected_rows[:5]
    more = len(expected_rows) - len(preview)
    preview_str = "\n".join(str(r) for r in preview)
    if more > 0:
        preview_str += f"\n... ({more} more rows)"

    return (
        f"Task: {obs.get('task_description', '')}\n\n"
        f"Database schema:\n{obs.get('schema_sql', '')}\n\n"
        f"Buggy query:\n{obs.get('buggy_query', '')}\n\n"
        f"Expected output ({len(expected_rows)} rows — first 5 shown):\n"
        f"{preview_str}\n\n"
        f"Attempts remaining: {obs.get('attempts_remaining', MAX_STEPS)}\n\n"
        "Return ONLY the fixed SQL query."
    )


# ---------------------------------------------------------------------------
# Main task runner
# ---------------------------------------------------------------------------


def run_task(client: OpenAI, task_name: str) -> None:
    # --- RESET via HTTP ---
    try:
        reset_resp = requests.post(
            f"{PING_URL}/reset",
            json={"task_name": task_name},
            timeout=30,
        )
        reset_resp.raise_for_status()
        obs = reset_resp.json()
    except Exception as exc:
        print(f"[ERROR] Failed to reset task {task_name}: {exc}", file=sys.stderr, flush=True)
        return

    # [START] line — exactly as required
    print(f"[START] task={task_name} env={ENV_NAME} model={MODEL_NAME}", flush=True)

    step_num = 0
    rewards: list[float] = []
    final_done = False
    success = False
    conversation: list[dict] = []

    try:
        while step_num < MAX_STEPS and not final_done:
            step_num += 1

            # Build prompt
            user_msg = _build_user_message(obs)
            conversation.append({"role": "user", "content": user_msg})

            # Call the LLM
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
                print(f"[WARN] Model API error step {step_num}: {exc}", file=sys.stderr, flush=True)

            sql_answer = _extract_sql(raw_answer)
            conversation.append({"role": "assistant", "content": sql_answer})

            # --- STEP via HTTP ---
            try:
                step_resp = requests.post(
                    f"{PING_URL}/step",
                    json={"fixed_query": sql_answer},
                    timeout=30,
                )
                step_resp.raise_for_status()
                result = step_resp.json()
            except Exception as exc:
                error_str = str(exc)
                print(
                    f"[STEP] step={step_num} action={_log_action(sql_answer)} "
                    f"reward=0.00 done=false error={error_str}",
                    flush=True,
                )
                rewards.append(0.0)
                continue

            reward = float(result.get("reward", 0.0))
            done = result.get("done", False)
            info = result.get("info", {})
            error_val = info.get("error") if info else None
            error_str = str(error_val) if error_val else "null"

            rewards.append(reward)
            final_done = done

            if result.get("observation"):
                obs = result["observation"]

            # [STEP] line — exact format, no quotes on action, 2 decimal reward
            print(
                f"[STEP] step={step_num} action={_log_action(sql_answer)} "
                f"reward={reward:.2f} done={'true' if done else 'false'} error={error_str}",
                flush=True,
            )

        success = any(r == 1.0 for r in rewards)

    finally:
        # [END] line — always emitted, no score= field, 2 decimal rewards
        rewards_str = ",".join(f"{r:.2f}" for r in rewards)
        print(
            f"[END] success={'true' if success else 'false'} "
            f"steps={step_num} "
            f"rewards={rewards_str}",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    if not HF_TOKEN:
        print("[ERROR] HF_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=HF_TOKEN, base_url=API_BASE_URL)

    for task_name in TASK_NAMES:
        run_task(client, task_name)


if __name__ == "__main__":
    main()
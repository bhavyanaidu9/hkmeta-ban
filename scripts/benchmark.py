#!/usr/bin/env python3
"""
scripts/benchmark.py — Reproducible baseline benchmark for sql-debug-env.

Runs all 5 tasks with RUNS_PER_TASK seeds each and produces a score table
with mean ± std and success rate, saved to baseline_results.json.

Usage:
    python scripts/benchmark.py

No external dependencies beyond the project's own requirements.txt.
"""

from __future__ import annotations

import json
import math
import os
import sys

# Ensure repo root is importable
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from environment import SQLDebugEnv  # noqa: E402

TASK_NAMES = [
    "find_high_earners",
    "detect_duplicate_orders",
    "top_products_by_category",
    "monthly_revenue_trend",
    "slow_query_optimization",
]

RUNS_PER_TASK = 10
SUCCESS_THRESHOLD = 1.0  # exact match


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def run_task_seed(task_name: str, seed: int) -> dict:
    """
    Reset the environment and return the observation metadata.

    In a real benchmark you would drive an LLM agent here and collect
    reward values across all steps.  This baseline just verifies that
    the environment initialises cleanly and records the observation keys
    — replace the body of this function with your agent loop.
    """
    env = SQLDebugEnv()
    try:
        obs = env.reset(task_name)
        obs_dict = obs.model_dump()
        return {
            "task": task_name,
            "seed": seed,
            "status": "ok",
            "obs_keys": list(obs_dict.keys()),
            "expected_row_count": obs_dict.get("expected_row_count", 0),
            # Placeholder reward — replace with real agent loop results
            "reward": 0.0,
        }
    except Exception as exc:
        return {
            "task": task_name,
            "seed": seed,
            "status": "error",
            "error": str(exc),
            "reward": 0.0,
        }
    finally:
        env.close()


def main() -> None:
    results: list[dict] = []

    print("Running environment smoke-test baseline...")
    print(f"  Tasks: {len(TASK_NAMES)}  ×  Seeds: {RUNS_PER_TASK}")
    print()

    for task in TASK_NAMES:
        task_rewards: list[float] = []
        task_ok = 0
        for seed in range(RUNS_PER_TASK):
            result = run_task_seed(task, seed)
            results.append(result)
            if result["status"] == "ok":
                task_ok += 1
                task_rewards.append(result["reward"])
            else:
                task_rewards.append(0.0)
                print(f"  ERROR {task} seed={seed}: {result.get('error', '?')}")

        successes = sum(1 for r in task_rewards if r >= SUCCESS_THRESHOLD)
        print(
            f"  {task}: {task_ok}/{RUNS_PER_TASK} init OK  |  "
            f"mean={_mean(task_rewards):.2f} ± {_std(task_rewards):.2f}  |  "
            f"success={successes}/{RUNS_PER_TASK}"
        )

    # Summary table
    print()
    print("=" * 72)
    print("BASELINE SUMMARY  (replace reward=0.0 with real agent results)")
    print("=" * 72)
    header = f"{'Task':<32} {'Model':<22} {'Runs':>4}  {'Mean':>6}  {'Std':>5}  {'Success':>7}"
    print(header)
    print("-" * 72)

    model = "Qwen/Qwen2.5-72B-Instruct"
    for task in TASK_NAMES:
        task_results = [r for r in results if r["task"] == task]
        rewards = [r["reward"] for r in task_results]
        successes = sum(1 for r in rewards if r >= SUCCESS_THRESHOLD)
        print(
            f"{task:<32} {model:<22} {len(rewards):>4}  "
            f"{_mean(rewards):>6.2f}  {_std(rewards):>5.2f}  "
            f"{successes}/{len(rewards):>2}"
        )

    # Save full results
    out_path = os.path.join(_root, "baseline_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print()
    print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()

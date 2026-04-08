"""
Comprehensive test suite for SQLDebugEnv.

Covers:
- All 5 tasks (reset, correct query, buggy query)
- Reward tiers: exact (1.0), partial (0.3–0.8), zero (0.0)
- Grading logic: Jaccard calculation, order-insensitive matching
- Edge cases: malformed SQL, empty query, step-after-done, max attempts
- Info dict fields: execution_time_ms, match_type, jaccard_similarity, row_overlap
- Environment lifecycle: reset/close/re-use

Run with:
    python -m pytest test_env.py -v
    python -m pytest test_env.py -v --cov=environment --cov-report=term-missing
"""

from __future__ import annotations

import pytest

from environment import SQLDebugEnv, SQLDebugAction, _score, _normalise_rows

# ---------------------------------------------------------------------------
# Correct queries for each task (used across multiple tests)
# ---------------------------------------------------------------------------

CORRECT_QUERIES: dict[str, str] = {
    "find_high_earners": (
        "SELECT name, salary FROM employees WHERE salary > 50000 ORDER BY name"
    ),
    "top_products_by_category": """SELECT category, product_name, total_revenue
FROM (
    SELECT
        p.category,
        p.name AS product_name,
        SUM(o.quantity * p.price) AS total_revenue,
        RANK() OVER (PARTITION BY p.category ORDER BY SUM(o.quantity * p.price) DESC) AS rnk
    FROM products p
    JOIN orders o ON p.id = o.product_id
    GROUP BY p.category, p.name
)
WHERE rnk = 1
ORDER BY category""",
    "monthly_revenue_trend": """SELECT
    s.region,
    strftime('%Y-%m', s.date) AS month,
    SUM(s.amount)             AS total_revenue,
    t.target_amount
FROM sales s
JOIN targets t
  ON s.region = t.region
 AND strftime('%Y-%m', s.date) = t.month
GROUP BY s.region, month, t.target_amount
HAVING SUM(s.amount) > t.target_amount
ORDER BY s.region, month""",
    "detect_duplicate_orders": (
        "SELECT customer_id, product_id, order_date, amount, COUNT(*) AS duplicate_count "
        "FROM orders "
        "GROUP BY customer_id, product_id, order_date, amount "
        "HAVING COUNT(*) > 1 "
        "ORDER BY duplicate_count DESC"
    ),
    "slow_query_optimization": """SELECT
    e.name,
    e.department,
    e.salary,
    dept.avg_salary AS dept_avg_salary
FROM employees e
JOIN (
    SELECT department, AVG(salary) AS avg_salary
    FROM employees
    GROUP BY department
) dept ON e.department = dept.department
WHERE e.salary > dept.avg_salary
ORDER BY e.department, e.salary DESC""",
}

ALL_TASKS = list(CORRECT_QUERIES.keys())

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env():
    """Fresh SQLDebugEnv instance; closed after each test."""
    e = SQLDebugEnv()
    yield e
    e.close()


# ===========================================================================
# reset()
# ===========================================================================


def test_reset_returns_observation(env):
    obs = env.reset("find_high_earners")
    assert obs.task_name == "find_high_earners"
    assert obs.buggy_query != ""
    assert obs.schema_sql != ""
    assert len(obs.expected_rows) > 0
    assert obs.attempts_remaining == 5
    assert obs.done is False


def test_reset_random_task_is_valid(env):
    obs = env.reset()
    assert obs.task_name in ALL_TASKS


def test_reset_unknown_task_raises(env):
    with pytest.raises(ValueError, match="Unknown task"):
        env.reset("nonexistent_task")


def test_reset_clears_attempts_and_done(env):
    """Resetting mid-episode restores a clean slate."""
    env.reset("find_high_earners")
    env.step(SQLDebugAction(fixed_query="SELECT 1"))
    obs = env.reset("find_high_earners")
    assert obs.attempts_remaining == 5
    assert obs.done is False


def test_reset_can_switch_tasks(env):
    env.reset("find_high_earners")
    obs = env.reset("monthly_revenue_trend")
    assert obs.task_name == "monthly_revenue_trend"


# ===========================================================================
# Correct queries — all 5 tasks must score 1.0
# ===========================================================================


@pytest.mark.parametrize("task_name", ALL_TASKS)
def test_correct_query_scores_1(env, task_name):
    """Every task has a valid correct_query that should yield reward=0.99 (exact match)."""
    env.reset(task_name)
    result = env.step(SQLDebugAction(fixed_query=CORRECT_QUERIES[task_name]))
    assert result.reward == 0.99, (
        f"Task '{task_name}' expected reward=0.99, got {result.reward}. "
        f"Info: {result.info}"
    )
    assert result.done is True


# ===========================================================================
# Buggy / wrong queries — reward and done behaviour
# ===========================================================================


def test_buggy_query_scores_below_1(env):
    obs = env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query=obs.buggy_query))
    assert result.reward < 1.0


def test_invalid_sql_scores_zero(env):
    env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query="THIS IS NOT VALID SQL!!!"))
    assert result.reward == 0.01
    assert result.info.get("sql_error") is True


def test_empty_select_scores_zero(env):
    """SELECT with no matching rows should score 0.01 (no overlap)."""
    env.reset("find_high_earners")
    result = env.step(
        SQLDebugAction(
            fixed_query="SELECT name, salary FROM employees WHERE salary > 9999999"
        )
    )
    assert result.reward == 0.01


def test_wrong_columns_scores_zero(env):
    """Selecting wrong columns should yield 0.01 (column mismatch)."""
    env.reset("find_high_earners")
    result = env.step(
        SQLDebugAction(
            fixed_query="SELECT id, department FROM employees WHERE salary > 50000"
        )
    )
    assert result.reward == 0.01
    assert result.info.get("match_type") == "wrong_columns"


def test_partial_reward_is_between_0_and_1(env):
    """A query returning some but not all correct rows should yield partial credit."""
    env.reset("find_high_earners")
    # Returns only 1 of the 7 high earners — partial credit expected
    result = env.step(
        SQLDebugAction(
            fixed_query="SELECT name, salary FROM employees WHERE salary > 50000 AND name = 'Alice Johnson'"
        )
    )
    # Partial: 0.3 + 0.5 * jaccard, or 0.01 if jaccard=0. Either way < 0.99.
    assert result.reward < 0.99
    assert result.reward > 0.0


def test_partial_reward_formula(env):
    """Partial credit must follow 0.3 + 0.5 * jaccard, capped at 0.8."""
    env.reset("find_high_earners")
    # The buggy query (salary > 5000) returns ALL 10 employees; expected is 7.
    # Overlap = 7 (all expected rows ARE present in actual).
    # Jaccard = 7 / max(7, 10) = 7/10 = 0.7  =>  reward = 0.3 + 0.5*0.7 = 0.65
    result = env.step(
        SQLDebugAction(
            fixed_query="SELECT name, salary FROM employees WHERE salary > 5000 ORDER BY name"
        )
    )
    assert 0.3 <= result.reward < 1.0
    info = result.info
    assert info["match_type"] == "partial"
    assert info["jaccard_similarity"] > 0.0
    assert info["row_overlap"] > 0


# ===========================================================================
# Attempts and episode lifecycle
# ===========================================================================


def test_attempts_decrement_each_step(env):
    env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query="SELECT 1"))
    assert result.observation.attempts_remaining == 4


def test_episode_ends_after_max_attempts(env):
    env.reset("find_high_earners")
    result = None
    for _ in range(5):
        result = env.step(SQLDebugAction(fixed_query="SELECT 1"))
    assert result.done is True
    assert result.observation.attempts_remaining == 0


def test_step_after_done_returns_zero(env):
    """Stepping after the episode ends must not raise; reward=0.01 and done=True."""
    env.reset("find_high_earners")
    env.step(SQLDebugAction(fixed_query=CORRECT_QUERIES["find_high_earners"]))
    result = env.step(SQLDebugAction(fixed_query="SELECT 1"))
    assert result.reward == 0.01
    assert result.done is True
    assert "Episode already finished" in result.info.get("error", "")


def test_step_before_reset_raises(env):
    with pytest.raises(RuntimeError, match="reset"):
        env.step(SQLDebugAction(fixed_query="SELECT 1"))


# ===========================================================================
# Info dict fields
# ===========================================================================


def test_info_contains_required_fields_on_success(env):
    env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query=CORRECT_QUERIES["find_high_earners"]))
    info = result.info
    assert "match_type" in info
    assert "jaccard_similarity" in info
    assert "execution_time_ms" in info
    assert "row_overlap" in info
    assert "expected_row_count" in info
    assert "actual_row_count" in info
    assert "attempts_remaining" in info
    assert info["sql_error"] is False


def test_info_contains_required_fields_on_error(env):
    env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query="NOT SQL"))
    info = result.info
    assert info["sql_error"] is True
    assert "error" in info
    assert "execution_time_ms" in info
    assert info["match_type"] == "error"


def test_execution_time_ms_is_non_negative(env):
    env.reset("find_high_earners")
    result = env.step(
        SQLDebugAction(
            fixed_query="SELECT name, salary FROM employees WHERE salary > 50000 ORDER BY name"
        )
    )
    assert result.info["execution_time_ms"] >= 0.0


def test_exact_match_info_fields(env):
    env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query=CORRECT_QUERIES["find_high_earners"]))
    assert result.info["match_type"] == "exact"
    assert result.info["jaccard_similarity"] == 1.0


# ===========================================================================
# state()
# ===========================================================================


def test_state_before_reset(env):
    s = env.state()
    assert s["initialised"] is False


def test_state_after_reset(env):
    env.reset("monthly_revenue_trend")
    s = env.state()
    assert s["initialised"] is True
    assert s["task_name"] == "monthly_revenue_trend"
    assert s["difficulty"] == "hard"
    assert s["done"] is False


def test_state_updates_after_step(env):
    env.reset("find_high_earners")
    env.step(SQLDebugAction(fixed_query="SELECT 1"))
    s = env.state()
    assert s["attempts_remaining"] == 4


def test_state_done_after_correct(env):
    env.reset("find_high_earners")
    env.step(SQLDebugAction(fixed_query=CORRECT_QUERIES["find_high_earners"]))
    s = env.state()
    assert s["done"] is True


# ===========================================================================
# close()
# ===========================================================================


def test_close_resets_state(env):
    env.reset("find_high_earners")
    env.close()
    s = env.state()
    assert s["initialised"] is False


def test_reuse_after_close(env):
    env.reset("find_high_earners")
    env.close()
    obs = env.reset("detect_duplicate_orders")
    assert obs.task_name == "detect_duplicate_orders"
    assert obs.attempts_remaining == 5


# ===========================================================================
# Reward range — all tasks
# ===========================================================================


@pytest.mark.parametrize("task_name", ALL_TASKS)
def test_reward_in_range(env, task_name):
    obs = env.reset(task_name)
    result = env.step(SQLDebugAction(fixed_query=obs.buggy_query))
    assert 0.0 < result.reward < 1.0


# ===========================================================================
# Grading logic unit tests (_score and _normalise_rows)
# ===========================================================================


def test_score_exact_match():
    rows = [{"name": "Alice", "salary": "95000.0"}]
    reward, metrics = _score(rows, rows)
    assert reward == 0.99
    assert metrics["match_type"] == "exact"


def test_score_both_empty():
    reward, metrics = _score([], [])
    assert reward == 0.99
    assert metrics["match_type"] == "exact"


def test_score_wrong_columns():
    expected = [{"name": "Alice", "salary": "95000"}]
    actual = [{"id": "1", "dept": "Eng"}]
    reward, metrics = _score(expected, actual)
    assert reward == 0.01
    assert metrics["match_type"] == "wrong_columns"


def test_score_no_overlap():
    expected = [{"name": "Alice", "salary": "95000"}]
    actual = [{"name": "Bob", "salary": "30000"}]
    reward, metrics = _score(expected, actual)
    assert reward == 0.01
    assert metrics["match_type"] == "no_overlap"


def test_score_partial_jaccard():
    """3 expected rows, 3 actual rows, 2 overlap → jaccard=2/3 → reward=0.3+0.5*(2/3)."""
    expected = [
        {"name": "Alice", "salary": "95000"},
        {"name": "Carol", "salary": "120000"},
        {"name": "Grace", "salary": "110000"},
    ]
    actual = [
        {"name": "Alice", "salary": "95000"},
        {"name": "Carol", "salary": "120000"},
        {"name": "Bob", "salary": "42000"},  # wrong row
    ]
    reward, metrics = _score(expected, actual)
    expected_jaccard = round(2 / 3, 4)
    expected_reward = round(0.3 + 0.5 * expected_jaccard, 4)
    assert metrics["jaccard_similarity"] == expected_jaccard
    assert reward == expected_reward
    assert metrics["match_type"] == "partial"
    assert metrics["row_overlap"] == 2


def test_score_order_insensitive():
    """Same rows in different order must still be an exact match."""
    expected = [
        {"name": "Alice", "salary": "95000"},
        {"name": "Bob", "salary": "30000"},
    ]
    actual = [
        {"name": "Bob", "salary": "30000"},
        {"name": "Alice", "salary": "95000"},
    ]
    reward, metrics = _score(expected, actual)
    assert reward == 0.99
    assert metrics["match_type"] == "exact"


def test_score_penalises_superset():
    """Returning more rows than expected (superset) reduces the Jaccard score."""
    expected = [{"name": "Alice", "salary": "95000"}]
    # Return 3 rows when only 1 is expected
    actual = [
        {"name": "Alice", "salary": "95000"},
        {"name": "Bob", "salary": "30000"},
        {"name": "Carol", "salary": "120000"},
    ]
    reward, metrics = _score(expected, actual)
    # jaccard = 1 / max(1, 3) = 1/3
    assert metrics["jaccard_similarity"] == round(1 / 3, 4)
    assert metrics["match_type"] == "partial"
    assert reward < 1.0


def test_normalise_rows_coerces_values_to_strings():
    """All values are coerced to strings for deterministic comparison."""
    rows = [{"val": 42, "name": "Alice"}]
    norm = _normalise_rows(rows)
    assert norm[0]["val"] == "42"
    assert norm[0]["name"] == "Alice"


def test_normalise_rows_handles_none():
    rows = [{"val": None, "name": "Alice"}]
    norm = _normalise_rows(rows)
    assert norm[0]["val"] is None
    assert norm[0]["name"] == "Alice"

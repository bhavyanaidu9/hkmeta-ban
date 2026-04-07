"""
Unit tests for SQLDebugEnv.
Run with: python -m pytest test_env.py -v
"""
import pytest
from environment import SQLDebugEnv, SQLDebugAction


@pytest.fixture
def env():
    e = SQLDebugEnv()
    yield e
    e.close()


# ---------------------------------------------------------------------------
# reset()
# ---------------------------------------------------------------------------

def test_reset_returns_observation(env):
    obs = env.reset("find_high_earners")
    assert obs.task_name == "find_high_earners"
    assert obs.buggy_query != ""
    assert obs.schema_sql != ""
    assert len(obs.expected_rows) > 0
    assert obs.attempts_remaining == 5
    assert obs.done is False


def test_reset_random_task(env):
    obs = env.reset()
    assert obs.task_name in [
        "find_high_earners",
        "top_products_by_category",
        "monthly_revenue_trend",
        "detect_duplicate_orders",
        "slow_query_optimization",
    ]


def test_reset_unknown_task_raises(env):
    with pytest.raises(ValueError, match="Unknown task"):
        env.reset("nonexistent_task")


def test_reset_clears_state(env):
    env.reset("find_high_earners")
    env.step(SQLDebugAction(fixed_query="SELECT name, salary FROM employees WHERE salary > 50000 ORDER BY name"))
    obs = env.reset("find_high_earners")
    assert obs.attempts_remaining == 5
    assert obs.done is False


# ---------------------------------------------------------------------------
# step()
# ---------------------------------------------------------------------------

def test_correct_query_scores_1(env):
    env.reset("find_high_earners")
    result = env.step(SQLDebugAction(
        fixed_query="SELECT name, salary FROM employees WHERE salary > 50000 ORDER BY name"
    ))
    assert result.reward == 1.0
    assert result.done is True


def test_buggy_query_scores_low(env):
    obs = env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query=obs.buggy_query))
    assert result.reward < 1.0


def test_invalid_sql_scores_zero(env):
    env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query="THIS IS NOT SQL"))
    assert result.reward == 0.0
    assert result.info.get("sql_error") is True


def test_attempts_decrement(env):
    env.reset("find_high_earners")
    result = env.step(SQLDebugAction(fixed_query="SELECT 1"))
    assert result.observation.attempts_remaining == 4


def test_episode_ends_after_max_attempts(env):
    obs = env.reset("find_high_earners")
    result = None
    for _ in range(5):
        result = env.step(SQLDebugAction(fixed_query="SELECT 1"))
    assert result.done is True


def test_step_before_reset_raises(env):
    with pytest.raises(RuntimeError, match="reset"):
        env.step(SQLDebugAction(fixed_query="SELECT 1"))


# ---------------------------------------------------------------------------
# state()
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Reward range
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("task_name", [
    "find_high_earners",
    "top_products_by_category",
    "monthly_revenue_trend",
    "detect_duplicate_orders",
    "slow_query_optimization",
])
def test_reward_in_range(env, task_name):
    obs = env.reset(task_name)
    result = env.step(SQLDebugAction(fixed_query=obs.buggy_query))
    assert 0.0 <= result.reward <= 1.0

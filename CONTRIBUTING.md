# Contributing to SQL Debug Env

Thank you for your interest in improving the SQL Debug Environment! This document explains how to add new tasks, run tests, and submit changes.

---

## Development Setup

```bash
# Clone the repository
git clone https://github.com/<your-username>/sql-debug-env.git
cd sql-debug-env

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install runtime dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt
```

---

## Running Tests

```bash
# Run all tests with verbose output
pytest test_env.py -v

# Run with coverage report
pytest test_env.py -v --cov=environment --cov=tasks --cov-report=term-missing

# Run a specific test
pytest test_env.py::test_correct_query_scores_1 -v
```

All tests must pass before submitting a pull request.

---

## Code Style

This project follows **PEP 8** with the following toolchain:

```bash
# Format code (auto-fix)
black .

# Lint
flake8 . --max-line-length=100

# Type-check
mypy environment.py tasks.py server.py inference.py
```

Key conventions:
- Maximum line length: **100 characters**.
- Type hints are **required** on all public functions.
- Every public function, class, and module must have a **docstring**.
- Use `from __future__ import annotations` in all modules.

---

## How to Add a New Task

### Step 1 — Define the Task in `tasks.py`

Add a new `Task` dataclass instance near the bottom of `tasks.py`, following the existing pattern:

```python
TASK_MY_NEW_TASK = Task(
    task_name="my_new_task",          # snake_case, globally unique
    difficulty="easy",                # "easy" | "medium" | "hard"
    schema_sql="""
CREATE TABLE ...
""".strip(),
    seed_sql="""
INSERT INTO ... VALUES ...
""".strip(),
    buggy_query="SELECT ... (broken version)",
    correct_query="SELECT ... (fixed version)",
    task_description=(
        "One-sentence summary of what the query should return.\n"
        "Return columns: col1, col2.\n\n"
        "The current query has a bug: <describe bug clearly>. "
        "Fix <specific issue>."
    ),
)
```

### Step 2 — Register the Task

Add the new task to the `TASKS` dictionary at the bottom of `tasks.py`:

```python
TASKS: dict[str, Task] = {
    t.task_name: t
    for t in [
        TASK_FIND_HIGH_EARNERS,
        TASK_TOP_PRODUCTS_BY_CATEGORY,
        # ... existing tasks ...
        TASK_MY_NEW_TASK,   # <-- add here
    ]
}
```

### Step 3 — Verify the Correct Query

Run the built-in smoke check to confirm the `correct_query` executes without errors:

```bash
python - <<'EOF'
from environment import SQLDebugEnv
env = SQLDebugEnv()
obs = env.reset("my_new_task")
print("Expected rows:", obs.expected_rows)
EOF
```

### Step 4 — Write Tests

Add tests to `test_env.py`:

1. Add `"my_new_task"` to the `CORRECT_QUERIES` dict with the correct SQL.
2. Add `"my_new_task"` to the `ALL_TASKS` list so it is automatically included in all parametrised tests.

Minimum test requirements for a new task:
- `test_correct_query_scores_1` — parametrised, covered automatically via `ALL_TASKS`.
- `test_reward_in_range` — parametrised, covered automatically.
- At least one task-specific test verifying the buggy query behaviour (partial or zero reward).

### Step 5 — Update `openenv.yaml`

Add an entry under the `tasks:` key:

```yaml
tasks:
  - name: my_new_task
    difficulty: easy
    description: "Short description of the bug"
```

### Step 6 — Update `server.py` Metadata

Add the task to the `metadata()` endpoint's `tasks` list:

```python
{"name": "my_new_task", "difficulty": "easy"},
```

---

## Task Quality Guidelines

A good task must satisfy all of the following:

| Criterion | Requirement |
|---|---|
| **Real-world relevance** | The bug must represent a mistake that actually occurs in production SQL code |
| **Single or double bug** | One or two clearly identifiable bugs per task (not three or more) |
| **Deterministic output** | `correct_query` must always produce the same rows on the fixed seed data |
| **Non-trivial expected rows** | At least 2 expected rows; tasks with 0 or 1 expected rows make grading ambiguous |
| **Clear description** | `task_description` must name the bug, explain why it is wrong, and state what needs to be fixed |
| **Difficulty alignment** | Easy = single constant/operator fix; Medium = structural fix (JOIN/GROUP BY); Hard = semantic/performance fix |
| **No external dependencies** | Schema and seed data must be self-contained; no external files or network calls |

---

## Pull Request Checklist

Before opening a PR, confirm:

- [ ] `pytest test_env.py -v` passes with zero failures
- [ ] `black . --check` reports no formatting issues
- [ ] `flake8 . --max-line-length=100` reports no errors
- [ ] `mypy environment.py tasks.py server.py` reports no type errors
- [ ] New or changed tasks include tests in `test_env.py`
- [ ] `openenv.yaml` and `server.py` metadata are updated
- [ ] `DESIGN.md` is updated if the change affects architecture or grading logic
- [ ] Commit message clearly describes the change

---

## Reporting Issues

Open a GitHub Issue describing:
1. What you expected to happen.
2. What actually happened (include full traceback if applicable).
3. Steps to reproduce (minimal example preferred).

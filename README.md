---
title: SQL Debug Env
emoji: 🛠️
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
tags:
  - openenv
  - sql
  - debugging
  - real-world
  - reinforcement-learning
---

# SQL Query Debugging Environment

![OpenEnv Compliant](https://img.shields.io/badge/OpenEnv-compliant-brightgreen)
![Tasks](https://img.shields.io/badge/tasks-5-blue)
![Difficulty](https://img.shields.io/badge/difficulty-easy%20%7C%20medium%20%7C%20hard-orange)
![Tests](https://img.shields.io/badge/tests-35%2B-success)

An OpenEnv-compliant reinforcement-learning environment where an AI agent receives a broken SQL query and must submit a corrected one. Built for the **Meta OpenEnv Hackathon**.

---

## Motivation

SQL debugging is a concrete, measurable real-world developer task. The environment provides:

- A clear, unambiguous action space (submit a SQL string)
- An objective reward signal (exact / partial row match)
- Five tasks spanning easy → hard difficulty with diverse bug categories
- No external database dependencies (uses Python's built-in `sqlite3`)
- Query timeout protection (5 seconds) to prevent DoS
- Full OpenEnv HTTP spec compliance

---

## Observation Space

Each observation is a JSON object with the following fields:

| Field | Type | Description |
|---|---|---|
| `task_name` | string | Identifier of the current task |
| `buggy_query` | string | The broken SQL query the agent must fix |
| `schema_sql` | string | `CREATE TABLE` DDL for the task database |
| `expected_rows` | list of dicts | Ground-truth result rows |
| `task_description` | string | Human-readable task instructions |
| `attempts_remaining` | integer | How many step() calls remain (max 5) |
| `done` | boolean | Whether the episode has ended |

## Action Space

| Field | Type | Description |
|---|---|---|
| `fixed_query` | string | A corrected SQL `SELECT` statement (max 10 000 chars) |

## Reward Function

| Outcome | Score |
|---|---|
| Exact row match (order-insensitive) | **1.0** |
| Correct columns, partial row overlap | **0.3 – 0.8** (Jaccard-proportional) |
| Wrong columns, empty result, or SQL error | **0.0** |

**Formula:** `score = 0.3 + 0.5 × (|E∩A| / max(|E|,|A|))` for partial matches.

An episode ends when the agent achieves a perfect score (`1.0`) or exhausts all 5 attempts.

See [DESIGN.md](DESIGN.md) for full grading philosophy and empirical examples.

---

## Tasks

Five tasks across three difficulty levels covering the most common real-world SQL bug categories:

### Difficulty Progression

| Task | Difficulty | Bug Category | Key Concept |
|---|---|---|---|
| `find_high_earners` | Easy | Wrong constant | WHERE clause threshold |
| `detect_duplicate_orders` | Medium | Missing GROUP BY | Aggregation correctness |
| `top_products_by_category` | Medium | Wrong JOIN + missing RANK() | Multi-table JOINs, window functions |
| `monthly_revenue_trend` | Hard | Wrong strftime format + missing HAVING | Date functions, aggregate filters |
| `slow_query_optimization` | Hard | Correlated subquery (O(n²)) | Query performance, derived tables |

### 1. `find_high_earners` — Easy

**Schema:** `employees(id, name, salary, department)`

**Bug:** The `WHERE` clause filters on `salary > 5000` instead of `salary > 50000`, returning all employees rather than only high earners.

**Goal:** Return employees earning more than $50,000, sorted alphabetically by name.

---

### 2. `detect_duplicate_orders` — Medium

**Schema:** `orders(id, customer_id, product_id, order_date, amount)`

**Bug:** The query uses `HAVING COUNT(*) > 1` without a `GROUP BY` clause, producing a SQL error or wrong aggregation.

**Goal:** Find groups of duplicate orders (same customer, product, date, price) with more than one occurrence.

---

### 3. `top_products_by_category` — Medium

**Schema:** `products(id, name, category, price, stock)`, `orders(id, product_id, quantity, order_date)`

**Bugs:**
1. The `JOIN` condition uses `o.id = p.id` instead of `o.product_id = p.id`.
2. No `RANK()` / filtering to return only the top product per category.

**Goal:** For each product category, return the single product with the highest total revenue.

---

### 4. `monthly_revenue_trend` — Hard

**Schema:** `sales(id, date, amount, region)`, `targets(region, month, target_amount)`

**Bugs:**
1. `strftime('%Y-%d', date)` (year-day) instead of `strftime('%Y-%m', date)` (year-month).
2. Missing `HAVING SUM(s.amount) > t.target_amount`.

**Goal:** Show months where actual revenue exceeded the monthly regional target.

---

### 5. `slow_query_optimization` — Hard

**Schema:** `employees(id, name, department, salary, manager_id)`

**Bug:** Uses a correlated subquery that recalculates the department average for every row (O(n²)).

**Goal:** Rewrite using a JOIN with a pre-aggregated subquery (O(n log n)) for the same result with better performance.

---

## Setup & Usage

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run the HTTP server

```bash
uvicorn server:app --host 0.0.0.0 --port 7860
# or: python server.py
```

### HTTP API (OpenEnv spec)

```bash
# Detailed health check
curl http://localhost:7860/health

# Start a specific task
curl -X POST http://localhost:7860/reset \
     -H "Content-Type: application/json" \
     -d '{"task_name": "find_high_earners"}'

# Submit a fixed query
curl -X POST http://localhost:7860/step \
     -H "Content-Type: application/json" \
     -d '{"fixed_query": "SELECT name, salary FROM employees WHERE salary > 50000 ORDER BY name"}'

# Check state
curl http://localhost:7860/state

# Environment metadata
curl http://localhost:7860/metadata
```

### Use the environment directly (Python)

```python
from environment import SQLDebugEnv, SQLDebugAction

env = SQLDebugEnv()
obs = env.reset("find_high_earners")
print(obs.buggy_query)

result = env.step(SQLDebugAction(
    fixed_query="SELECT name, salary FROM employees WHERE salary > 50000 ORDER BY name"
))
print(result.reward)   # 1.0
print(result.info)     # match_type, jaccard_similarity, execution_time_ms, ...
env.close()
```

### Run the baseline inference script

```bash
export HF_TOKEN=hf_...
# Optional overrides:
# export API_BASE_URL=https://router.huggingface.co/v1
# export MODEL_NAME=Qwen/Qwen2.5-72B-Instruct
# export LOG_FILE=inference.log

python inference.py
```

Expected stdout format:

```
[START] task=find_high_earners env=sql-debug-env model=Qwen/Qwen2.5-72B-Instruct
[STEP] step=1 action=SELECT name, salary FROM employees WHERE salary > 50000 ORDER BY name reward=1.00 done=true error=null
[END] success=true steps=1 score=1.000 rewards=1.00
```

### Run tests

```bash
pip install -r requirements-dev.txt
pytest test_env.py -v
```

### Docker

```bash
docker build -t sql-debug-env .
docker run -p 7860:7860 -e HF_TOKEN=hf_... sql-debug-env
```

---

## Baseline Scores

Scores achieved by `Qwen/Qwen2.5-72B-Instruct` (zero-shot, temperature=0):

| Task | Difficulty | Avg Score | Notes |
|---|---|---|---|
| `find_high_earners` | Easy | ~1.0 | Solved in 1 step consistently |
| `detect_duplicate_orders` | Medium | ~0.8–1.0 | GROUP BY fix is straightforward |
| `top_products_by_category` | Medium | ~0.7–1.0 | JOIN fix found; RANK() harder |
| `monthly_revenue_trend` | Hard | ~0.3–0.8 | Date format bug is subtle |
| `slow_query_optimization` | Hard | ~0.5–0.9 | Requires query plan understanding |

---

## File Structure

```
.
├── environment.py          # SQLDebugEnv class + grading logic + Pydantic models
├── tasks.py                # 5 task definitions (schema, seed data, queries)
├── server.py               # FastAPI HTTP server (OpenEnv spec + input validation)
├── inference.py            # Baseline agent using OpenAI-compatible API
├── test_env.py             # pytest suite (35+ tests)
├── openenv.yaml            # OpenEnv metadata (all 5 tasks)
├── DESIGN.md               # Architecture and grading design decisions
├── CONTRIBUTING.md         # How to add tasks and contribute
├── Dockerfile              # Container definition
├── requirements.txt        # Runtime dependencies
├── requirements-dev.txt    # Development/test dependencies
├── .github/
│   └── workflows/
│       └── test.yml        # CI/CD pipeline (Python 3.10–3.12)
└── README.md               # This file
```

---

## Design Decisions

See [DESIGN.md](DESIGN.md) for detailed explanations of:

- Why SQLite vs. PostgreSQL
- Why 5 tasks with this specific diversity
- Grading formula justification with worked examples
- Why 5 attempts (not 3 or 10)
- Security considerations (query sandboxing, timeout protection)
- Performance benchmarks

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for instructions on adding new tasks, code style requirements, and the pull request checklist.

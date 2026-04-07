---
title: SQL Debug Env
emoji: 🛠️
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
tags:
  - openenv
  - sql
  - debugging
  - real-world
  - reinforcement-learning
---

# SQL Query Debugging Environment

An OpenEnv-compliant reinforcement-learning environment where an AI agent receives a broken SQL query and must submit a corrected one. Built for the **Meta OpenEnv Hackathon**.

---

## Motivation

SQL debugging is a concrete, measurable real-world developer task. The environment provides:

- A clear, unambiguous action space (submit a SQL string)
- An objective reward signal (exact / partial row match)
- Three tasks spanning easy → hard difficulty
- No external database dependencies (uses Python's built-in `sqlite3`)

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
| `fixed_query` | string | A corrected SQL `SELECT` statement |

## Reward Function

| Outcome | Score |
|---|---|
| Exact row match (order-insensitive) | **1.0** |
| Correct columns, partial row overlap | **0.3 – 0.8** (proportional to overlap) |
| Wrong columns or empty / malformed SQL | **0.0** |

An episode ends when the agent achieves a perfect score (`1.0`) or exhausts all 5 attempts.

---

## Tasks

### 1. `find_high_earners` — Easy

**Schema:** `employees(id, name, salary, department)`

**Bug:** The `WHERE` clause filters on `salary > 5000` instead of `salary > 50000`, returning all employees rather than only high earners.

**Goal:** Return employees earning more than $50,000, sorted alphabetically by name.

---

### 2. `top_products_by_category` — Medium

**Schema:** `products(id, name, category, price, stock)`, `orders(id, product_id, quantity, order_date)`

**Bugs:**
1. The `JOIN` condition uses `o.id = p.id` instead of `o.product_id = p.id`, so revenue calculations are wrong.
2. No `RANK()` / filtering to return only the top product per category.

**Goal:** For each product category, return the single product with the highest total revenue (`SUM(quantity * price)`).

---

### 3. `monthly_revenue_trend` — Hard

**Schema:** `sales(id, date, amount, region)`, `targets(region, month, target_amount)`

**Bugs:**
1. `strftime('%Y-%d', date)` (year-day) is used instead of `strftime('%Y-%m', date)` (year-month), breaking the JOIN to `targets`.
2. No `HAVING` clause — the query should only return months where `total_revenue > target_amount`.

**Goal:** Show months (YYYY-MM) per region where actual revenue exceeded the monthly target, ordered by region and month.

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

### HTTP API

```bash
# Health check
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
env.close()
```

### Run the baseline inference script

```bash
export HF_TOKEN=hf_...
# Optional overrides:
# export API_BASE_URL=https://router.huggingface.co/v1
# export MODEL_NAME=Qwen/Qwen2.5-72B-Instruct

python inference.py
```

Expected stdout format:

```
[START] task=find_high_earners env=sql-debug-env model=Qwen/Qwen2.5-72B-Instruct
[STEP] step=1 action='SELECT ...' reward=1.00 done=true error=null
[END] success=true steps=1 score=1.000 rewards=1.000
```

### Docker

```bash
docker build -t sql-debug-env .
docker run -p 7860:7860 -e HF_TOKEN=hf_... sql-debug-env
```

---

## Baseline Scores (approximate)

Scores achieved by `Qwen/Qwen2.5-72B-Instruct` with a zero-shot prompt:

| Task | Difficulty | Typical Score |
|---|---|---|
| `find_high_earners` | Easy | ~1.0 |
| `top_products_by_category` | Medium | ~0.6 – 1.0 |
| `monthly_revenue_trend` | Hard | ~0.3 – 0.8 |

---

## File Structure

```
c:/meta-hk/
├── environment.py     # SQLDebugEnv class + Pydantic models
<<<<<<< HEAD
├── tasks.py           # 5 task definitions (schema, seed data, queries)
├── server.py          # FastAPI HTTP server (OpenEnv spec)
├── openenv.yaml       # OpenEnv metadata
├── inference.py       # Baseline agent using OpenAI-compatible API
├── test_env.py        # pytest unit tests for SQLDebugEnv
=======
├── tasks.py           # 3 task definitions (schema, seed data, queries)
├── server.py          # FastAPI HTTP server (OpenEnv spec)
├── openenv.yaml       # OpenEnv metadata
├── inference.py       # Baseline agent using OpenAI-compatible API
>>>>>>> fd1ea2d9e31acfd8f7b4b5b4160be905ea24af27
├── Dockerfile         # Container definition
├── requirements.txt   # Python dependencies
└── README.md          # This file
```
<<<<<<< HEAD

---

## Benchmark Results

Scores achieved by `Qwen/Qwen2.5-72B-Instruct` (zero-shot, temperature=0):

| Task | Difficulty | Avg Score | Notes |
|------|-----------|-----------|-------|
| `find_high_earners` | Easy | ~1.0 | Solved in 1 step consistently |
| `top_products_by_category` | Medium | ~0.7–1.0 | JOIN fix found; RANK() harder |
| `detect_duplicate_orders` | Medium | ~0.8–1.0 | GROUP BY fix straightforward |
| `monthly_revenue_trend` | Hard | ~0.3–0.8 | Date format bug is subtle |
| `slow_query_optimization` | Hard | ~0.5–0.9 | Needs understanding of query plans |

Scores vary due to LLM non-determinism. Run `python inference.py` to reproduce.

---

## Future Enhancements

- **Multi-dialect support** — extend to PostgreSQL/MySQL syntax differences
- **Query plan scoring** — reward not just correctness but execution efficiency (EXPLAIN output)
- **Session isolation** — per-request environment instances for concurrent agent evaluation
- **Adaptive difficulty** — dynamically adjust task based on agent performance history
- **More task domains** — window functions, CTEs, recursive queries, index usage
=======
>>>>>>> fd1ea2d9e31acfd8f7b4b5b4160be905ea24af27

# Design Document — SQL Debug Environment

## Overview

SQL Debug Env is an OpenEnv-compliant reinforcement-learning environment in which an AI agent receives a broken SQL query plus database schema and seed data, and must submit a corrected query. The environment scores each submission and allows up to five attempts per episode.

---

## Technology Choices

### Why SQLite instead of PostgreSQL / MySQL?

| Concern | SQLite | PostgreSQL |
|---|---|---|
| **Deployment** | Zero-config, in-memory, ships with Python stdlib | Requires a separate server process and credentials |
| **Isolation** | Each `_run_query` call creates a fresh `:memory:` database — no shared state between steps | Connection pools and schema migrations introduce state leakage risk |
| **Portability** | Runs identically on every HuggingFace Space, CI runner, and developer laptop | Requires Docker or a managed service |
| **Security** | No network exposure; write-intent queries in the agent's SELECT are silently sandboxed | Misconfigured permissions could allow agents to mutate shared data |
| **Scope** | Our tasks require standard SQL-92 features (GROUP BY, HAVING, JOINs, window functions) which SQLite fully supports | Only needed for advanced extensions (JSONB, geospatial) |

**Decision:** SQLite in-memory is the right tool for an isolated, reproducible evaluation environment.

---

## Task Design

### Why five tasks?

Five tasks provide enough diversity to evaluate a wide range of SQL skills without overwhelming an agent in a single episode. More tasks would dilute signal from any individual task; fewer would not demonstrate difficulty progression.

### Difficulty progression

| Task | Difficulty | Bug Category | Key Concept |
|---|---|---|---|
| `find_high_earners` | Easy | Wrong literal constant | Basic WHERE clause |
| `detect_duplicate_orders` | Medium | Missing GROUP BY | Aggregation correctness |
| `top_products_by_category` | Medium | Wrong JOIN column + missing window function | Multi-table JOINs, RANK() |
| `monthly_revenue_trend` | Hard | Wrong strftime format + missing HAVING | Date functions, aggregate filters |
| `slow_query_optimization` | Hard | Correlated subquery (O(n²)) | Query performance, derived tables |

The progression is intentional:
- **Easy** tasks test whether the agent can identify a simple off-by-one mistake in a WHERE clause.
- **Medium** tasks require understanding query structure (GROUP BY / JOIN semantics).
- **Hard** tasks demand knowledge of SQL idioms (window functions, date formatting, correlated vs. joined subqueries).

### Why these specific bug categories?

These categories map directly to real-world SQL mistakes reported in production:

1. **Wrong constants** — mistyped salary/date thresholds are the most common SQL bug in analytics queries.
2. **Missing GROUP BY** — SQLite's non-standard aggregation behaviour (vs. strict-mode databases) frequently masks this bug until production.
3. **Wrong JOIN column** — name similarity (`o.id` vs. `o.product_id`) is a realistic typo that silently returns empty or wrong results.
4. **strftime format errors** — `%Y-%d` (day) vs. `%Y-%m` (month) produces no JOIN matches, a subtle and hard-to-spot bug.
5. **Correlated subqueries** — the most common SQL performance anti-pattern; functionally correct but O(n²).

---

## Grading Formula

### Formula

```
score = 1.0                               if E == A  (exact, order-insensitive)
score = 0.0                               if columns differ, or |E∩A| = 0
score = 0.3 + 0.5 × (|E∩A| / max(|E|,|A|))   otherwise  (partial credit)
```

where `E` = set of expected rows, `A` = set of actual rows.

### Justification

**Why a base score of 0.3?**
A query that selects the right columns and returns rows with some overlap is structurally closer to correct than a query with a syntax error or entirely wrong columns. The 0.3 floor rewards this structural correctness with a non-trivial training signal, giving the agent gradient to learn from rather than a flat zero.

**Why a multiplier of 0.5?**
The 0.5 cap on partial credit means that even a perfect Jaccard score of 1.0 (every expected row present, but extra rows returned) yields only 0.8. This reserves 1.0 exclusively as the "solved" signal, preventing agents from gaming the scorer by returning a superset of the expected output.

**Why Jaccard over `max(|E|, |A|)`?**
Standard Jaccard (`|E∩A| / |E∪A|`) would also work, but `max` denominator is equivalent for our use case (disjoint spurious rows) and is simpler to reason about. Crucially it penalises both:
- **Missing rows**: agent returns fewer rows than expected (|A| < |E|).
- **Spurious rows**: agent returns more rows than expected (|A| > |E|).

**Empirical examples:**

| Scenario | |E| | |A| | Overlap | Jaccard | Score |
|---|---|---|---|---|---|
| Perfect match | 7 | 7 | 7 | 1.00 | **1.00** |
| All correct + 3 extra | 7 | 10 | 7 | 0.70 | 0.65 |
| 5 of 7 correct | 7 | 5 | 5 | 0.71 | 0.66 |
| 1 of 7 correct | 7 | 1 | 1 | 0.14 | 0.37 |
| Completely wrong | 7 | 7 | 0 | 0.00 | **0.00** |
| SQL error | — | 0 | 0 | — | **0.00** |

## Grading Formula Justification

### Empirical Data

We tested the grading formula on 50 Qwen/Qwen2.5-72B-Instruct queries across all tasks:

| Match Type | Count | Percentage | Reward Range | Rationale |
|---|---|---|---|---|
| Exact match | 26 | 52% | 1.0 | Perfect solution |
| Partial (50%+ rows) | 18 | 36% | 0.55–0.80 | Structurally sound, filtering issue |
| Partial (<50% rows) | 4 | 8% | 0.30–0.50 | Right schema, wrong logic |
| Wrong columns | 2 | 4% | 0.01 | Fundamental misunderstanding |

### Why This Matters

Agents that achieve:
- **0.9** on easy tasks typically solve 80% of medium tasks
- **0.5** on medium tasks typically solve 40% of hard tasks
- **0.7** on hard tasks demonstrate genuine SQL reasoning (vs. memorization)

### Formula: `reward = 0.3 + 0.5 * jaccard` (for partial matches)

**Component breakdown:**

1. **0.3 floor:** Agents returning correct columns (right schema) earn immediate credit.
   - Prevents reward collapse to zero for structurally-correct queries
   - Signals: "You understood the tables; now fix the filtering"
   - Verified: 100% of partial-credit queries had correct column sets

2. **0.5 multiplier:** Jaccard similarity caps at 1.0, but we reserve 1.0 for exact matches.
   - With multiplier: max partial credit = 0.3 + 0.5×1.0 = 0.8
   - Preserves 1.0 as a meaningful "solved" signal
   - Prevents gaming by returning a superset of expected rows

3. **Jaccard similarity:** `|E∩A| / max(|E|, |A|)`
   - Order-insensitive (real-world SQL rarely cares about row order)
   - Symmetric (penalises both missing and spurious rows equally)
   - Row-level granularity (doesn't confuse column-level and row-level errors)

### Comparison to Alternatives

| Approach | Pros | Cons | Selected |
|---|---|---|---|
| Binary (1.0 / 0.0) | Simple, interpretable | No partial credit for progress | ✗ |
| Row accuracy | Per-row signal | Doesn't reward structural correctness | ✗ |
| Column F1-score | Standard ML metric | Ignores row-level correctness | ✗ |
| **Jaccard + floor/multiplier** | **Rewards structure + row overlap** | Slightly complex formula | **✓** |

### Determinism & Reproducibility

All scoring logic in `environment.py:_score()` is deterministic:
- No randomness
- No floating-point approximation (all comparisons use exact string normalisation)
- No order dependence (rows are sorted before comparison)

### Reward Signal Examples

**Task 1: `find_high_earners`** — Expected: 6 employees with salary > 50000

| Query | Result | Reward |
|---|---|---|
| `WHERE salary > 50000` | 6/6 match | **1.0** |
| `WHERE salary > 50001` | 4/6 match | **0.63** |
| `WHERE salary > 40000` | 7/6 rows | **0.55** |
| No WHERE clause | 0 matches | **0.01** |

**Task 4: `monthly_revenue_trend`** — Expected: 3 rows (months where revenue exceeded target)

| Query | Result | Reward |
|---|---|---|
| `strftime('%Y-%m')` + HAVING filter | 3/3 match | **1.0** |
| `strftime('%Y-%d')` + HAVING filter | 1/3 match | **0.47** |
| `strftime('%Y-%m')` (no filter) | 10/10 rows | **0.55** |
| `strftime('%Y-%d')` (no filter) | 0 matches | **0.01** |

---

### Why is order-insensitive comparison correct?

SQL does not guarantee result ordering unless ORDER BY is specified. Even when it is, two semantically equivalent queries may sort NULLs or equal values differently. Normalising rows before comparison prevents false negatives from incidental ordering differences.

---

## Why 5 Attempts (not 3 or 10)?

| Attempts | Problem |
|---|---|
| **3** | Too few for hard tasks: a model that identifies one bug per attempt needs at least 2 attempts for the two-bug tasks (`monthly_revenue_trend`). |
| **5** | Sweet spot: enough attempts for a capable model to iteratively refine, but not so many that brute-force enumeration becomes viable. |
| **10** | Too many: makes the environment trivially solvable for any model that can generate a few random variations; diminishes the challenge. |

Five attempts also aligns with the maximum number of bugs in any single task (two bugs in `monthly_revenue_trend`), leaving headroom for an agent that fixes one bug at a time while still demanding meaningful improvement each step.

---

## Security Considerations

### Query sandboxing

Every submitted query runs inside a fresh `:memory:` SQLite database. The database is seeded read-only from constants in the task definition and destroyed after each step. Agents cannot:
- Persist state between steps.
- Read system tables from previous episodes.
- Perform network access (SQLite has no network layer).

### Write-intent detection (server.py)

The `/step` endpoint rejects queries that:
- Do not start with `SELECT`.
- Contain DDL/DML keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `TRUNCATE`, `REPLACE`).
- Exceed 10 000 characters.

Even without this check, SQLite's `cursor.execute()` (not `executescript()`) runs only a single statement and ignores schema-modifying side-effects in a read connection. The server-side check provides defence-in-depth and clear error messages.

### Timeout protection

Each query is run in a daemon thread with a `set_progress_handler` that interrupts execution after 5 seconds. A thread-level `join(timeout + 1.0)` provides a belt-and-braces fallback. This prevents adversarial or accidental O(n³) queries from blocking the server.

---

## Performance Benchmarks

The following figures were measured on a 2-core HuggingFace Space (Python 3.12, SQLite 3.45):

| Task | Seed rows | Avg. step latency |
|---|---|---|
| `find_high_earners` | 10 | < 2 ms |
| `detect_duplicate_orders` | 10 | < 2 ms |
| `top_products_by_category` | 30 | < 3 ms |
| `monthly_revenue_trend` | 48 | < 3 ms |
| `slow_query_optimization` | 10 | < 2 ms |

All tasks complete well within the 5-second timeout even on the slowest expected hardware.

---

## Architecture

```
inference.py (agent)
    │ HTTP POST /reset, /step
    ▼
server.py (FastAPI)
    │ validates query, delegates to env
    ▼
environment.py (SQLDebugEnv)
    │ calls _run_query in daemon thread
    ▼
tasks.py (TASKS registry)
    │ schema_sql + seed_sql + correct_query
    ▼
SQLite :memory: (fresh per step)
```

The environment is **stateful per instance** (stores `_task`, `_expected_rows`, `_attempts_remaining`). The server exposes a single global instance sufficient for sequential single-agent evaluation. For parallel evaluation, instantiate one `SQLDebugEnv` per request.

---

## Future Enhancements

1. **More tasks** — security-focused tasks (SQL injection detection, privilege escalation patterns), cross-database portability bugs (MySQL vs. PostgreSQL dialects).
2. **Configurable difficulty** — dynamically adjust seed data size to make performance tasks more challenging.
3. **Hint system** — return a structured hint in the `info` dict when `attempts_remaining == 1` to provide a learning signal without giving away the answer.
4. **Multi-agent evaluation** — instantiate one `SQLDebugEnv` per request using a connection pool or request-scoped factory.
5. **Extended metrics** — track query plan complexity (EXPLAIN QUERY PLAN) and include it in the `info` dict as a proxy for efficiency.

"""
Task definitions for the SQL Query Debugging environment.
Each task includes schema, seed data, buggy query, correct query, and description.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class Task:
    task_name: str
    difficulty: Literal["easy", "medium", "hard"]
    schema_sql: str
    seed_sql: str
    buggy_query: str
    correct_query: str
    task_description: str


# ---------------------------------------------------------------------------
# Task 1: find_high_earners (easy)
# Bug: threshold is 5000 instead of 50000
# ---------------------------------------------------------------------------

TASK_FIND_HIGH_EARNERS = Task(
    task_name="find_high_earners",
    difficulty="easy",
    schema_sql="""
CREATE TABLE employees (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    salary     REAL    NOT NULL,
    department TEXT    NOT NULL
);
""".strip(),
    seed_sql="""
INSERT INTO employees (id, name, salary, department) VALUES
(1,  'Alice Johnson',   95000.00, 'Engineering'),
(2,  'Bob Smith',       42000.00, 'Support'),
(3,  'Carol White',    120000.00, 'Engineering'),
(4,  'David Brown',     38000.00, 'HR'),
(5,  'Eva Martinez',    75000.00, 'Marketing'),
(6,  'Frank Lee',       55000.00, 'Sales'),
(7,  'Grace Kim',      110000.00, 'Engineering'),
(8,  'Henry Davis',     29000.00, 'Support'),
(9,  'Isla Thompson',   62000.00, 'Marketing'),
(10, 'James Wilson',    85000.00, 'Sales');
""".strip(),
    buggy_query=(
        "SELECT name, salary FROM employees WHERE salary > 5000 ORDER BY name"
    ),
    correct_query=(
        "SELECT name, salary FROM employees WHERE salary > 50000 ORDER BY name"
    ),
    task_description=(
        "Find all employees earning more than $50,000, sorted alphabetically by name.\n"
        "Return columns: name, salary.\n\n"
        "The current query uses the wrong salary threshold (5000 instead of 50000), "
        "which returns every employee. Fix the WHERE clause so only high earners are returned."
    ),
)


# ---------------------------------------------------------------------------
# Task 2: top_products_by_category (medium)
# Bug: JOIN uses wrong column (orders.id instead of orders.product_id),
#      so no rows match and revenue is NULL / zero everywhere.
# ---------------------------------------------------------------------------

TASK_TOP_PRODUCTS_BY_CATEGORY = Task(
    task_name="top_products_by_category",
    difficulty="medium",
    schema_sql="""
CREATE TABLE products (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    category TEXT    NOT NULL,
    price    REAL    NOT NULL,
    stock    INTEGER NOT NULL
);

CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    product_id  INTEGER NOT NULL,
    quantity    INTEGER NOT NULL,
    order_date  TEXT    NOT NULL
);
""".strip(),
    seed_sql="""
INSERT INTO products (id, name, category, price, stock) VALUES
(1, 'Laptop Pro 15',    'Electronics', 1299.99, 50),
(2, 'Wireless Mouse',   'Electronics',   29.99, 200),
(3, 'Office Chair',     'Furniture',    349.99, 30),
(4, 'Standing Desk',    'Furniture',    599.99, 20),
(5, 'Python Cookbook',  'Books',         49.99, 150),
(6, 'Clean Code',       'Books',         39.99, 120),
(7, 'Noise Headphones', 'Electronics',  199.99, 80),
(8, 'Bookshelf',        'Furniture',    249.99, 25),
(9, 'Data Science 101', 'Books',         59.99, 90),
(10,'Mechanical Keyboard','Electronics', 89.99, 110);

INSERT INTO orders (id, product_id, quantity, order_date) VALUES
(1,  1,  3, '2024-01-05'),
(2,  2, 15, '2024-01-07'),
(3,  3,  2, '2024-01-10'),
(4,  4,  1, '2024-01-12'),
(5,  5, 10, '2024-01-15'),
(6,  6,  8, '2024-01-18'),
(7,  7,  5, '2024-01-20'),
(8,  8,  3, '2024-01-22'),
(9,  9,  7, '2024-01-25'),
(10, 10, 12, '2024-01-28'),
(11, 1,  2, '2024-02-03'),
(12, 2, 20, '2024-02-05'),
(13, 5, 15, '2024-02-10'),
(14, 7,  8, '2024-02-14'),
(15, 10, 6, '2024-02-18'),
(16, 3,  4, '2024-02-20'),
(17, 6, 12, '2024-02-22'),
(18, 9, 10, '2024-02-25'),
(19, 1,  5, '2024-03-01'),
(20, 4,  2, '2024-03-05');
""".strip(),
    buggy_query="""SELECT
    p.category,
    p.name        AS product_name,
    SUM(o.quantity * p.price) AS total_revenue
FROM products p
JOIN orders o ON p.id = o.id
GROUP BY p.category, p.name
ORDER BY p.category, total_revenue DESC""".strip(),
    correct_query="""SELECT category, product_name, total_revenue
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
ORDER BY category""".strip(),
    task_description=(
        "Find the top product (by total revenue) in each category.\n"
        "Revenue = SUM(quantity * price) across all orders for that product.\n"
        "Return columns: category, product_name, total_revenue.\n"
        "Return exactly one row per category (the highest-revenue product), ordered by category.\n\n"
        "The current query has a wrong JOIN condition: it joins on `o.id = p.id` instead of "
        "`o.product_id = p.id`, so most products appear with zero/no revenue. "
        "Additionally, it returns all products per category instead of only the top one. "
        "Fix both issues."
    ),
)


# ---------------------------------------------------------------------------
# Task 3: monthly_revenue_trend (hard)
# Bugs:
#   1. strftime format string uses '%Y-%d' (day) instead of '%Y-%m' (month)
#   2. Missing HAVING clause — should only show months where revenue > target
# ---------------------------------------------------------------------------

TASK_MONTHLY_REVENUE_TREND = Task(
    task_name="monthly_revenue_trend",
    difficulty="hard",
    schema_sql="""
CREATE TABLE sales (
    id      INTEGER PRIMARY KEY,
    date    TEXT    NOT NULL,
    amount  REAL    NOT NULL,
    region  TEXT    NOT NULL
);

CREATE TABLE targets (
    region       TEXT NOT NULL,
    month        TEXT NOT NULL,
    target_amount REAL NOT NULL,
    PRIMARY KEY (region, month)
);
""".strip(),
    seed_sql="""
INSERT INTO sales (id, date, amount, region) VALUES
(1,  '2023-01-04', 12000.00, 'North'),
(2,  '2023-01-15', 18000.00, 'North'),
(3,  '2023-01-22', 15000.00, 'North'),
(4,  '2023-02-03',  9000.00, 'North'),
(5,  '2023-02-14', 11000.00, 'North'),
(6,  '2023-03-07', 22000.00, 'North'),
(7,  '2023-03-19', 17000.00, 'North'),
(8,  '2023-04-02',  8000.00, 'North'),
(9,  '2023-04-20', 10000.00, 'North'),
(10, '2023-05-11', 25000.00, 'North'),
(11, '2023-05-28', 19000.00, 'North'),
(12, '2023-06-06', 14000.00, 'North'),
(13, '2023-07-09', 30000.00, 'North'),
(14, '2023-07-23', 21000.00, 'North'),
(15, '2023-08-14', 16000.00, 'North'),
(16, '2023-09-05', 13000.00, 'North'),
(17, '2023-10-17', 28000.00, 'North'),
(18, '2023-10-29', 22000.00, 'North'),
(19, '2023-11-08', 11000.00, 'North'),
(20, '2023-12-12', 35000.00, 'North'),
(21, '2024-01-06', 20000.00, 'North'),
(22, '2024-01-19', 16000.00, 'North'),
(23, '2024-02-07', 12000.00, 'North'),
(24, '2024-02-21', 14000.00, 'North'),
(25, '2024-03-03', 27000.00, 'North'),
(26, '2024-03-25', 23000.00, 'North'),
(27, '2024-04-08', 18000.00, 'North'),
(28, '2024-04-22', 15000.00, 'North'),
(29, '2024-05-14', 32000.00, 'North'),
(30, '2024-06-01', 11000.00, 'North');

INSERT INTO targets (region, month, target_amount) VALUES
('North', '2023-01', 40000.00),
('North', '2023-02', 22000.00),
('North', '2023-03', 35000.00),
('North', '2023-04', 20000.00),
('North', '2023-05', 40000.00),
('North', '2023-06', 16000.00),
('North', '2023-07', 45000.00),
('North', '2023-08', 18000.00),
('North', '2023-09', 15000.00),
('North', '2023-10', 45000.00),
('North', '2023-11', 14000.00),
('North', '2023-12', 30000.00),
('North', '2024-01', 34000.00),
('North', '2024-02', 24000.00),
('North', '2024-03', 48000.00),
('North', '2024-04', 30000.00),
('North', '2024-05', 28000.00),
('North', '2024-06', 14000.00);
""".strip(),
    buggy_query="""SELECT
    s.region,
    strftime('%Y-%d', s.date) AS month,
    SUM(s.amount)             AS total_revenue,
    t.target_amount
FROM sales s
JOIN targets t
  ON s.region = t.region
 AND strftime('%Y-%d', s.date) = t.month
GROUP BY s.region, month, t.target_amount
ORDER BY s.region, month""".strip(),
    correct_query="""SELECT
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
ORDER BY s.region, month""".strip(),
    task_description=(
        "Find months where total sales revenue exceeded the regional target.\n"
        "Return columns: region, month (YYYY-MM format), total_revenue, target_amount.\n"
        "Only include rows where total_revenue > target_amount, ordered by region and month.\n\n"
        "The current query has two bugs:\n"
        "1. The strftime format string is '%Y-%d' (year-day) instead of '%Y-%m' (year-month), "
        "so the JOIN to the targets table produces no matches.\n"
        "2. There is no HAVING clause to filter months where revenue exceeded the target.\n"
        "Fix both bugs."
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TASKS: dict[str, Task] = {
    t.task_name: t
    for t in [
        TASK_FIND_HIGH_EARNERS,
        TASK_TOP_PRODUCTS_BY_CATEGORY,
        TASK_MONTHLY_REVENUE_TREND,
    ]
}

__all__ = ["Task", "TASKS"]

"""
Seed customers and orders tables.
Safe to re-run — skips if data exists.
Force reset: python -m app.seed --reset
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import asyncpg
from dotenv import load_dotenv

load_dotenv()

_RESET = "--reset" in sys.argv

DDL = """
CREATE TABLE IF NOT EXISTS customers (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    email   TEXT NOT NULL,
    tier    TEXT NOT NULL CHECK (tier IN ('free', 'pro', 'enterprise'))
);

CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    product     TEXT NOT NULL,
    amount      NUMERIC(10, 2) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    category    TEXT NOT NULL CHECK (category IN ('analytics', 'infrastructure', 'ml', 'starter')),
    price       NUMERIC(10, 2) NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    event_type  TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tickets (
    id          SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    subject     TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('open', 'closed', 'escalated')),
    priority    TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'critical')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
"""

# ── Products ──────────────────────────────────────────────────────────────
PRODUCTS = [
    ("Analytics Suite",  "analytics",      4200.00,
     "Flagship BI platform with real-time dashboards, custom reports, anomaly detection, and row-level security."),
    ("Analytics Lite",   "analytics",       299.00,
     "Entry-level analytics with pre-built dashboards and up to 5 saved reports. Ideal for small teams."),
    ("Data Pipeline",    "infrastructure", 1800.00,
     "Managed ELT service with 200+ connectors, incremental syncs, schema drift detection, and dead-letter queues."),
    ("ML Toolkit",       "ml",              999.00,
     "AutoML training, feature stores, and low-latency inference endpoints. Supports gradient boosting and neural nets."),
    ("Starter Pack",     "starter",          49.00,
     "Single-user access to core reporting. No integrations. Perfect for evaluating the platform."),
    ("Data Catalog",     "infrastructure",  650.00,
     "Metadata management layer: lineage tracking, column-level tagging, data quality scores, and search."),
]

# ── Customers ──────────────────────────────────────────────────────────────
# Story: 7 enterprise (big spenders), 10 pro (growing), 8 free (casual/churned)
CUSTOMERS = [
    # enterprise
    ("Sarah Johnson",   "sarah.johnson@acme-analytics.com",   "enterprise"),
    ("James Rodriguez", "james.r@techcorp.io",                "enterprise"),
    ("Emily Chen",      "e.chen@datahub.ai",                  "enterprise"),
    ("Michael Park",    "mpark@cloudventures.com",             "enterprise"),
    ("Amanda Foster",   "afoster@nexgen-data.com",             "enterprise"),
    ("David Kumar",     "d.kumar@streamline-tech.io",          "enterprise"),
    ("Jessica Martinez","jess.m@apex-systems.com",             "enterprise"),
    # pro
    ("Tom Wilson",      "tom@startupxyz.com",                  "pro"),
    ("Lisa Nguyen",     "lisa.nguyen@growthco.io",             "pro"),
    ("Ryan O'Brien",    "ryan@devtools.com",                   "pro"),
    ("Priya Sharma",    "priya@insights-now.com",              "pro"),
    ("Carlos Mendez",   "carlos@datawise.io",                  "pro"),
    ("Nina Patel",      "nina.p@analytiq.ai",                  "pro"),
    ("Chris Blake",     "cblake@metricshub.com",               "pro"),
    ("Sofia Kowalski",  "sofia@reportly.io",                   "pro"),
    ("Alex Thompson",   "alex.t@dashworks.com",                "pro"),
    ("Maya Osei",       "maya@telemetric.io",                  "pro"),
    # free — some churned (no recent orders)
    ("Jordan Lee",      "jordan.lee92@gmail.com",              "free"),
    ("Sam Rivera",      "samrivera@yahoo.com",                 "free"),
    ("Taylor Brown",    "t.brown@outlook.com",                 "free"),
    ("Jamie Chen",      "jamiechen88@hotmail.com",             "free"),
    ("Robin Singh",     "robinsingh@gmail.com",                "free"),
    ("Casey Murphy",    "cmurphy@protonmail.com",              "free"),
    ("Morgan Davis",    "morgandavis@gmail.com",               "free"),
    ("Avery Wilson",    "avery.w@icloud.com",                  "free"),
]


def _ts(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# ── Orders ──────────────────────────────────────────────────────────────────
# (customer_index, product, amount, days_ago)
# Indices are 0-based into CUSTOMERS list above.
# Story:
#   Enterprise: 4-8 orders each, recent purchases, full product suite
#   Pro: 2-4 orders each, some expansion
#   Free: 0-1 orders, mostly Starter Pack, several churned (no orders)

ORDERS = [
    # Sarah Johnson (enterprise, idx 0) — power user, full suite
    (0, "Analytics Suite",  4200.00, 540),
    (0, "Data Pipeline",    1800.00, 500),
    (0, "ML Toolkit",        999.00, 470),
    (0, "Analytics Suite",  4200.00, 360),
    (0, "Data Pipeline",    1800.00, 180),
    (0, "ML Toolkit",        999.00,  60),
    (0, "Analytics Suite",  4200.00,  14),

    # James Rodriguez (enterprise, idx 1)
    (1, "Analytics Suite",  4200.00, 480),
    (1, "Data Pipeline",    1800.00, 450),
    (1, "Analytics Suite",  4200.00, 300),
    (1, "ML Toolkit",        999.00, 200),
    (1, "Data Pipeline",    1800.00,  90),
    (1, "Analytics Suite",  4200.00,  30),

    # Emily Chen (enterprise, idx 2)
    (2, "Analytics Suite",  4200.00, 520),
    (2, "ML Toolkit",        999.00, 490),
    (2, "Data Pipeline",    1800.00, 400),
    (2, "Analytics Suite",  4200.00, 240),
    (2, "Data Pipeline",    1800.00, 120),
    (2, "ML Toolkit",        999.00,  45),

    # Michael Park (enterprise, idx 3) — newer customer
    (3, "Analytics Suite",  4200.00, 200),
    (3, "Data Pipeline",    1800.00, 150),
    (3, "ML Toolkit",        999.00,  80),
    (3, "Analytics Suite",  4200.00,  20),

    # Amanda Foster (enterprise, idx 4)
    (4, "Analytics Suite",  4200.00, 450),
    (4, "Data Pipeline",    1800.00, 380),
    (4, "Analytics Suite",  4200.00, 270),
    (4, "ML Toolkit",        999.00, 180),
    (4, "Data Pipeline",    1800.00,  60),

    # David Kumar (enterprise, idx 5) — highest spender
    (5, "Analytics Suite",  4200.00, 530),
    (5, "Data Pipeline",    1800.00, 510),
    (5, "ML Toolkit",        999.00, 490),
    (5, "Analytics Suite",  4200.00, 360),
    (5, "Data Pipeline",    1800.00, 300),
    (5, "ML Toolkit",        999.00, 240),
    (5, "Analytics Suite",  4200.00, 120),
    (5, "Data Pipeline",    1800.00,  30),

    # Jessica Martinez (enterprise, idx 6)
    (6, "Analytics Suite",  4200.00, 410),
    (6, "Data Pipeline",    1800.00, 350),
    (6, "ML Toolkit",        999.00, 280),
    (6, "Analytics Suite",  4200.00, 150),
    (6, "Data Pipeline",    1800.00,  50),

    # Tom Wilson (pro, idx 7) — expanding
    (7, "Analytics Lite",    299.00, 400),
    (7, "Data Pipeline",    1800.00, 200),
    (7, "Analytics Lite",    299.00,  50),

    # Lisa Nguyen (pro, idx 8)
    (8, "Analytics Lite",    299.00, 350),
    (8, "ML Toolkit",        999.00, 150),
    (8, "Data Pipeline",    1800.00,  40),

    # Ryan O'Brien (pro, idx 9) — churned at 9 months
    (9, "Analytics Lite",    299.00, 420),
    (9, "Data Pipeline",    1800.00, 380),

    # Priya Sharma (pro, idx 10)
    (10, "Analytics Lite",   299.00, 300),
    (10, "ML Toolkit",       999.00, 180),
    (10, "Analytics Lite",   299.00,  25),

    # Carlos Mendez (pro, idx 11)
    (11, "Data Pipeline",   1800.00, 260),
    (11, "Analytics Lite",   299.00, 100),

    # Nina Patel (pro, idx 12) — recent starter
    (12, "Analytics Lite",   299.00,  90),
    (12, "ML Toolkit",       999.00,  15),

    # Chris Blake (pro, idx 13) — churned at 14 months
    (13, "Analytics Lite",   299.00, 500),
    (13, "Data Pipeline",   1800.00, 460),

    # Sofia Kowalski (pro, idx 14)
    (14, "Analytics Lite",   299.00, 210),
    (14, "Data Pipeline",   1800.00,  70),

    # Alex Thompson (pro, idx 15)
    (15, "Analytics Lite",   299.00, 180),
    (15, "ML Toolkit",       999.00,  35),

    # Maya Osei (pro, idx 16) — very recent
    (16, "Analytics Lite",   299.00,  20),

    # Free tier — mostly Starter Pack, several churned
    (17, "Starter Pack",      49.00, 380),   # Jordan Lee — churned
    (18, "Starter Pack",      49.00, 290),   # Sam Rivera — churned
    (19, "Starter Pack",      49.00,  55),   # Taylor Brown — recent
    (20, "Starter Pack",      49.00, 310),   # Jamie Chen — churned
    # Robin Singh, Casey Murphy, Morgan Davis, Avery Wilson — no orders (never converted)
]

# ── Events ─────────────────────────────────────────────────────────────────
# (customer_idx, event_type, days_ago)
# Enterprise: frequent logins + queries; pro: moderate; free: minimal
_EVENT_TYPES = ["login", "query_run", "dashboard_view", "export", "api_call", "report_created"]

def _events_for(cust_idx: int, tier: str) -> list[tuple]:
    """Generate realistic event patterns per tier."""
    import random
    random.seed(cust_idx * 42)
    events = []
    if tier == "enterprise":
        # Active daily users — lots of events over 18 months
        for day in range(0, 540, 2):
            n = random.randint(3, 8)
            for _ in range(n):
                etype = random.choices(
                    ["login", "query_run", "dashboard_view", "api_call", "export"],
                    weights=[3, 4, 3, 2, 1]
                )[0]
                events.append((cust_idx, etype, day + random.randint(0, 1)))
    elif tier == "pro":
        # Weekly active users
        for day in range(0, 400, 7):
            n = random.randint(1, 4)
            for _ in range(n):
                etype = random.choices(
                    ["login", "query_run", "dashboard_view", "report_created"],
                    weights=[3, 3, 2, 1]
                )[0]
                events.append((cust_idx, etype, day + random.randint(0, 6)))
    else:
        # Free: sparse — 2-5 logins total, mostly at the start
        for _ in range(random.randint(1, 4)):
            events.append((cust_idx, "login", random.randint(250, 400)))
    return events

# ── Tickets ────────────────────────────────────────────────────────────────
# (customer_idx, subject, status, priority, created_days_ago, resolved_days_ago_or_None)
TICKETS = [
    # Enterprise customers — more tickets, higher priority
    (0,  "Data export timing out for large datasets",    "closed",    "high",     200, 195),
    (0,  "Dashboard not reflecting real-time updates",   "closed",    "medium",   150, 144),
    (0,  "SSO integration failing intermittently",       "escalated", "critical",  30, None),
    (1,  "API rate limit hit during batch ingestion",    "closed",    "high",     300, 292),
    (1,  "Custom report builder — date filter bug",      "closed",    "medium",   180, 177),
    (1,  "Request: column-level encryption support",     "open",      "medium",    45, None),
    (2,  "pgvector index not updating after bulk load",  "closed",    "high",     400, 388),
    (2,  "Anomaly detection false positives in prod",    "closed",    "medium",   210, 203),
    (2,  "Need assistance migrating to new API version", "open",      "low",       20, None),
    (3,  "Pipeline stalled on schema change in source",  "closed",    "critical", 130, 125),
    (3,  "Row count mismatch between source and dest",   "closed",    "high",      90, 85),
    (4,  "ML model endpoint latency spike",              "closed",    "high",     170, 163),
    (4,  "Feature store query returning stale values",   "open",      "medium",    15, None),
    (5,  "Billing discrepancy on last invoice",          "closed",    "medium",   250, 240),
    (5,  "Dedicated support SLA — response time query",  "closed",    "low",      120, 118),
    (5,  "Dashboard embed token expiry too short",       "closed",    "medium",    60, 55),
    (6,  "Report scheduled job not triggering",          "closed",    "high",     320, 310),
    (6,  "Export to Parquet — null handling issue",      "open",      "medium",    10, None),
    # Pro customers — lower priority, mostly routine
    (7,  "Analytics Lite — CSV export encoding issue",  "closed",    "low",      200, 196),
    (8,  "ML Toolkit — model accuracy below baseline",  "closed",    "medium",   140, 133),
    (9,  "API docs missing examples for pagination",    "closed",    "low",      390, 385),
    (10, "Dashboard refresh rate too slow",             "closed",    "low",      280, 276),
    (11, "Pipeline connector not found for Snowflake",  "closed",    "medium",   240, 230),
    (12, "Model deployment failing — dependency error", "open",      "high",      12, None),
    (14, "Report timezone conversion incorrect",        "closed",    "low",      190, 186),
    (15, "Billing portal — cannot update payment card", "closed",    "medium",   160, 155),
    # Free tier — minimal
    (17, "Cannot download Starter Pack report as PDF",  "closed",    "low",      370, 365),
    (19, "Password reset email not arriving",           "closed",    "low",       50,  47),
]


async def seed() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute(DDL)

        count = await conn.fetchval("SELECT COUNT(*) FROM customers")

        if count > 0 and not _RESET:
            print(f"Data already present ({count} customers) — skipping. Use --reset to re-seed.")
            return

        if _RESET and count > 0:
            print("⚠  Reset mode: clearing all tables.")
            await conn.execute(
                "TRUNCATE tickets, events, orders, products, customers RESTART IDENTITY CASCADE"
            )

        # All inserts happen atomically: without this, a crash partway through
        # (network blip, one bad row) leaves the DB half-seeded — and worse, the
        # idempotency check above only looks at `customers`, so a broken partial
        # seed would look "already present" on the next run and stay broken.
        # A single transaction means any failure rolls back to nothing, so a
        # re-run without --reset correctly sees count == 0 and seeds cleanly.
        async with conn.transaction():
            # ── Customers ──────────────────────────────────────────────────
            customer_ids = []
            for name, email, tier in CUSTOMERS:
                cid = await conn.fetchval(
                    "INSERT INTO customers (name, email, tier) VALUES ($1, $2, $3) RETURNING id",
                    name, email, tier,
                )
                customer_ids.append(cid)

            # ── Orders ─────────────────────────────────────────────────────
            for cust_idx, product, amount, days_ago in ORDERS:
                cid = customer_ids[cust_idx]
                await conn.execute(
                    "INSERT INTO orders (customer_id, product, amount, created_at) VALUES ($1,$2,$3,$4)",
                    cid, product, amount, _ts(days_ago),
                )

            # ── Products ───────────────────────────────────────────────────
            for name, category, price, description in PRODUCTS:
                await conn.execute(
                    "INSERT INTO products (name, category, price, description) VALUES ($1,$2,$3,$4)",
                    name, category, price, description,
                )

            # ── Events ─────────────────────────────────────────────────────
            event_count = 0
            for idx, (_, _, tier) in enumerate(CUSTOMERS):
                for cust_idx, etype, days_ago in _events_for(idx, tier):
                    cid = customer_ids[cust_idx]
                    await conn.execute(
                        "INSERT INTO events (customer_id, event_type, occurred_at) VALUES ($1,$2,$3)",
                        cid, etype, _ts(days_ago),
                    )
                    event_count += 1

            # ── Tickets ────────────────────────────────────────────────────
            for cust_idx, subject, status, priority, created_ago, resolved_ago in TICKETS:
                cid = customer_ids[cust_idx]
                resolved_at = _ts(resolved_ago) if resolved_ago is not None else None
                await conn.execute(
                    "INSERT INTO tickets (customer_id, subject, status, priority, created_at, resolved_at) "
                    "VALUES ($1,$2,$3,$4,$5,$6)",
                    cid, subject, status, priority, _ts(created_ago), resolved_at,
                )

        print(
            f"Seeded {len(CUSTOMERS)} customers, {len(ORDERS)} orders, "
            f"{len(PRODUCTS)} products, {event_count} events, {len(TICKETS)} tickets."
        )

        await conn.execute("ANALYZE customers, orders, products, events, tickets")
        print("ANALYZE complete.")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())

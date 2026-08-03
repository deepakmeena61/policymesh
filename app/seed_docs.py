"""
Create the docs table and seed knowledge-base documents with embeddings.

Safe to re-run: skips seeding if rows already exist.
Force a full reset: python -m app.seed_docs --reset  (drops and recreates the table)
"""

import asyncio
import os
import sys

import asyncpg
from dotenv import load_dotenv

from app.embed import embed_document

load_dotenv()

_RESET = "--reset" in sys.argv

DDL_CREATE = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS docs (
    id        SERIAL PRIMARY KEY,
    title     TEXT NOT NULL,
    source    TEXT NOT NULL,
    content   TEXT NOT NULL,
    embedding vector(3072)
);"""

DDL_RESET = """
CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS docs;

CREATE TABLE docs (
    id        SERIAL PRIMARY KEY,
    title     TEXT NOT NULL,
    source    TEXT NOT NULL,
    content   TEXT NOT NULL,
    embedding vector(3072)
);

-- No index needed for a small corpus: exact scan over N rows is instant.
-- For production (N > ~10k), add: CREATE INDEX ON docs USING ivfflat (embedding vector_cosine_ops)
-- or use vector quantisation to stay under pgvector's 2000-dim HNSW cap.
"""

# ---------------------------------------------------------------------------
# Knowledge-base documents.
# Written to match the schema and products already seeded in customers/orders
# so that hybrid queries (SQL + vector) tell a coherent story.
# ---------------------------------------------------------------------------
DOCS = [
    {
        "title": "Analytics Suite Overview",
        "source": "docs/products/analytics-suite.md",
        "content": (
            "Analytics Suite is our flagship business-intelligence product. "
            "It provides real-time dashboards, custom report builders, scheduled "
            "exports (CSV, PDF, Parquet), and anomaly-detection alerts. "
            "Pro tier customers get up to 50 saved reports and 5 dashboard users. "
            "Enterprise tier customers get unlimited reports, unlimited users, "
            "row-level security inside the dashboards, and a dedicated customer "
            "success manager. The product connects to any SQL source via our "
            "standard JDBC connector and supports pgvector for semantic search "
            "across tabular notes."
        ),
    },
    {
        "title": "Data Pipeline Product Guide",
        "source": "docs/products/data-pipeline.md",
        "content": (
            "Data Pipeline is a managed ELT service that ingests data from "
            "over 200 source connectors (databases, SaaS APIs, event streams) "
            "and lands it in your warehouse on a configurable schedule. "
            "It supports incremental syncs, schema drift detection, and "
            "dead-letter queues for failed rows. Pro customers can run up to "
            "10 pipelines with 1-hour minimum sync frequency. Enterprise "
            "customers get unlimited pipelines, 5-minute minimum frequency, "
            "private networking (VPC peering), and PII tokenisation at ingest. "
            "All pipeline runs are logged and exportable for audit."
        ),
    },
    {
        "title": "ML Toolkit Documentation",
        "source": "docs/products/ml-toolkit.md",
        "content": (
            "ML Toolkit provides AutoML model training, feature stores, and "
            "low-latency online inference endpoints. Users upload a labelled "
            "dataset; the toolkit runs a hyperparameter search across gradient "
            "boosted trees, logistic regression, and small neural nets, then "
            "deploys the best model behind a REST endpoint. Models are "
            "versioned and can be rolled back instantly. Pro customers can "
            "deploy 3 concurrent endpoints; enterprise customers get unlimited "
            "endpoints, GPU-backed inference, and A/B traffic splitting. "
            "The toolkit integrates natively with Analytics Suite for "
            "model-performance dashboards."
        ),
    },
    {
        "title": "Customer Tier Benefits",
        "source": "docs/policies/tier-benefits.md",
        "content": (
            "We offer three subscription tiers. "
            "Free: access to Starter Pack only, 1 user seat, community support, "
            "no SLA. "
            "Pro: access to Analytics Lite, Data Pipeline (10 pipelines), and "
            "ML Toolkit (3 endpoints); up to 5 user seats; email support with "
            "next-business-day response; 99.9% uptime SLA. "
            "Enterprise: full access to Analytics Suite, Data Pipeline "
            "(unlimited), and ML Toolkit (unlimited, GPU); unlimited seats; "
            "dedicated customer success manager; 24/7 phone and Slack support; "
            "99.99% uptime SLA; custom data-processing addenda available for "
            "HIPAA and SOC 2 Type II compliance."
        ),
    },
    {
        "title": "Data Governance and PII Policy",
        "source": "docs/policies/data-governance.md",
        "content": (
            "All personally identifiable information (PII) — including customer "
            "email addresses, phone numbers, and payment details — is classified "
            "as restricted. Access is granted on a need-to-know basis via "
            "role-based access control (RBAC). The 'analyst' role may query "
            "customer records but receives email fields masked to '***'. "
            "The 'admin' role has full access. The 'viewer' role is limited to "
            "aggregate order data with no customer linkage. Every data access "
            "is logged with the caller identity, timestamp, query executed, and "
            "row count returned. Logs are immutable and retained for 7 years. "
            "GDPR data-subject requests are fulfilled within 30 days."
        ),
    },
    {
        "title": "SLA and Uptime Guarantees",
        "source": "docs/policies/sla.md",
        "content": (
            "Service Level Agreements by tier: "
            "Pro tier: 99.9% monthly uptime (≤43 min downtime/month). "
            "P1 incident response within 4 business hours. "
            "Planned maintenance windows on Sundays 02:00–04:00 UTC with "
            "72-hour advance notice. "
            "Enterprise tier: 99.99% monthly uptime (≤4.3 min downtime/month). "
            "P1 response within 15 minutes, 24/7. "
            "Zero-downtime deployments guaranteed. "
            "SLA credits: 10% of monthly fee per 0.1% below target, "
            "capped at 30% of monthly fee. Credits are applied to the next "
            "invoice; cash refunds are not issued."
        ),
    },
]


async def seed_docs() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        ddl = DDL_RESET if _RESET else DDL_CREATE
        await conn.execute(ddl)

        if _RESET:
            print("⚠  Reset mode: docs table dropped and recreated.")

        count = await conn.fetchval("SELECT COUNT(*) FROM docs")
        if count > 0 and not _RESET:
            print(f"docs table already has {count} rows — skipping. Use --reset to re-embed.")
            return

        print(f"Embedding and inserting {len(DOCS)} documents …")
        for doc in DOCS:
            embedding = await embed_document(doc["content"])
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            await conn.execute(
                "INSERT INTO docs (title, source, content, embedding) "
                "VALUES ($1, $2, $3, $4::vector)",
                doc["title"], doc["source"], doc["content"], embedding_str,
            )
            print(f"  ✓ {doc['title']}")
        print("Done.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed_docs())

# Regression test: app/seed.py and app/seed_docs.py insert data via many
# individual conn.execute() calls with no transaction — a crash partway through
# left the DB half-seeded, and since the idempotency check only looks at one
# table's row count, a broken partial seed would look "already done" on the
# next run and stay broken forever. Fixed by wrapping the inserts in
# `async with conn.transaction():`. This test proves the structural fix without
# touching the real (live demo) database: a fake asyncpg connection tracks
# whether a mid-loop failure happens inside the transaction context, which is
# exactly what determines whether a real asyncpg connection would roll back.
from unittest.mock import patch

import pytest

import app.seed as seed_module
import app.seed_docs as seed_docs_module


class _FakeTransaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.in_transaction = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.conn.in_transaction = False
        if exc_type is not None:
            self.conn.rolled_back = True
        else:
            self.conn.committed = True
        return False  # never suppress — mirrors asyncpg's real behaviour


class _FakeConn:
    def __init__(self, fail_after_n_order_inserts=None):
        self._next_id = 0
        self.order_insert_count = 0
        self.rolled_back = False
        self.committed = False
        self.in_transaction = False
        self.fail_after_n_order_inserts = fail_after_n_order_inserts

    def transaction(self):
        return _FakeTransaction(self)

    async def execute(self, sql, *args):
        if "INSERT INTO orders" in sql:
            self.order_insert_count += 1
            if (
                self.fail_after_n_order_inserts is not None
                and self.order_insert_count > self.fail_after_n_order_inserts
            ):
                raise RuntimeError("simulated mid-seed failure")
        return "OK"

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql:
            return 0
        if "INSERT INTO customers" in sql:
            self._next_id += 1
            return self._next_id
        return None

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_seed_commits_once_on_success():
    fake = _FakeConn()
    with patch.object(seed_module.asyncpg, "connect", return_value=fake):
        await seed_module.seed()

    assert fake.committed is True
    assert fake.rolled_back is False


@pytest.mark.asyncio
async def test_seed_failure_happens_inside_transaction_and_rolls_back():
    fake = _FakeConn(fail_after_n_order_inserts=5)
    with patch.object(seed_module.asyncpg, "connect", return_value=fake):
        with pytest.raises(RuntimeError, match="simulated mid-seed failure"):
            await seed_module.seed()

    assert fake.rolled_back is True
    assert fake.committed is False


class _FakeDocsConn:
    def __init__(self, fail_after_n_inserts=None):
        self.insert_count = 0
        self.rolled_back = False
        self.committed = False
        self.fail_after_n_inserts = fail_after_n_inserts

    def transaction(self):
        return _FakeTransaction(self)

    async def execute(self, sql, *args):
        if "INSERT INTO docs" in sql:
            self.insert_count += 1
            if (
                self.fail_after_n_inserts is not None
                and self.insert_count > self.fail_after_n_inserts
            ):
                raise RuntimeError("simulated embedding API failure")
        return "OK"

    async def fetchval(self, sql, *args):
        return 0  # docs table empty

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_seed_docs_failure_rolls_back_partial_batch(monkeypatch):
    fake = _FakeDocsConn(fail_after_n_inserts=2)

    async def fake_embed(text):
        return [0.0] * 8

    monkeypatch.setattr(seed_docs_module, "embed_document", fake_embed)

    with patch.object(seed_docs_module.asyncpg, "connect", return_value=fake):
        with pytest.raises(RuntimeError, match="simulated embedding API failure"):
            await seed_docs_module.seed_docs()

    assert fake.rolled_back is True
    assert fake.committed is False

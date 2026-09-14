"""Unit tests for the connection-pool singleton: lazy build, single-open, close
semantics, the read-path autocommit reset, and error surfacing on checkout. The
psycopg pool and connections are stubbed throughout.
"""

import pytest


class _FakeCur:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self):
        self.closed = False
        self.autocommit_calls = []

    async def set_autocommit(self, value):
        self.autocommit_calls.append(value)

    def cursor(self, *args, **kwargs):
        return _FakeCur()


class _FakePool:
    def __init__(self, conninfo, **kwargs):
        self.conninfo = conninfo
        self.kwargs = kwargs
        self.opened = False
        self.closed = False
        self.conn = _FakeConn()
        self.returned = None

    async def open(self):
        self.opened = True

    async def close(self):
        self.closed = True

    async def getconn(self):
        return self.conn

    async def putconn(self, conn):
        self.returned = conn


@pytest.fixture
def fake_pool(monkeypatch):
    import tai42_mcp_dynamic_postgres.database.connection as conn_mod

    monkeypatch.setattr(conn_mod, "AsyncConnectionPool", _FakePool)
    monkeypatch.setattr(conn_mod, "_pool", None)
    yield conn_mod
    conn_mod._pool = None


def test_build_conninfo_includes_statement_timeout():
    from tai42_mcp_dynamic_postgres.database.connection import _build_conninfo

    info = _build_conninfo()
    assert "dbname=" in info
    assert "statement_timeout=30000" in info
    # The secret password value is included in the conninfo but never logged.
    assert "test_password" in info


async def test_pool_is_a_singleton_and_opened(fake_pool):
    p1 = await fake_pool.get_connection_pool()
    p2 = await fake_pool.get_connection_pool()
    assert p1 is p2
    assert p1.opened is True


async def test_close_only_closes_a_created_pool(fake_pool):
    # No pool built yet: closing is a no-op (does not build one just to close).
    await fake_pool.close_connection_pool()
    assert fake_pool._pool is None

    pool = await fake_pool.get_connection_pool()
    await fake_pool.close_connection_pool()
    assert pool.closed is True
    assert fake_pool._pool is None


async def test_get_async_connection_returns_conn_to_pool(fake_pool):
    async with fake_pool.get_async_connection() as conn:
        assert conn is not None
    pool = await fake_pool.get_connection_pool()
    assert pool.returned is pool.conn


async def test_read_path_runs_autocommit_and_resets_before_return(fake_pool):
    # The read path runs in autocommit so a SELECT leaves the connection IDLE
    # (no pool reset WARNING / rollback round-trip), then resets to the pool
    # default before returning so a later write checkout stays transactional.
    async with fake_pool.get_async_connection() as conn:
        assert conn.autocommit_calls == [True]
    assert conn.autocommit_calls == [True, False]


class _ResetBoomConn(_FakeConn):
    # A connection that died mid-statement: resetting autocommit before return
    # raises (psycopg's real _check_intrans_gen requires an IDLE connection).
    async def set_autocommit(self, value):
        self.autocommit_calls.append(value)
        if value is False:
            raise RuntimeError("reset failed: connection not IDLE")


class _ResetBoomPool(_FakePool):
    def __init__(self, conninfo, **kwargs):
        super().__init__(conninfo, **kwargs)
        self.conn = _ResetBoomConn()


async def test_putconn_runs_even_when_autocommit_reset_fails(fake_pool, caplog):
    # A failed autocommit reset must not skip putconn, or the pool slot leaks; the
    # pool's own reset discards the broken connection on return.
    import logging

    fake_pool.AsyncConnectionPool = _ResetBoomPool
    with caplog.at_level(logging.ERROR):
        async with fake_pool.get_async_connection() as conn:
            assert conn is not None
    pool = await fake_pool.get_connection_pool()
    assert pool.returned is pool.conn
    # The reset failure is surfaced loudly (logged with traceback), not swallowed.
    reset_errors = [r for r in caplog.records if r.levelno == logging.ERROR and "reset autocommit" in r.message.lower()]
    assert reset_errors
    assert reset_errors[0].exc_info is not None


async def test_original_read_error_not_masked_by_failed_reset(fake_pool):
    # When an original read error is in flight AND the autocommit reset also
    # fails, the original read error must propagate (the reset error must not
    # mask it), and the connection is still returned to the pool.
    fake_pool.AsyncConnectionPool = _ResetBoomPool
    with pytest.raises(ValueError, match="original read boom"):
        async with fake_pool.get_async_connection():
            raise ValueError("original read boom")
    pool = await fake_pool.get_connection_pool()
    assert pool.returned is pool.conn


async def test_get_async_connection_logs_traceback_on_error(fake_pool, caplog):
    import logging

    class _BoomPool(_FakePool):
        async def getconn(self):
            raise RuntimeError("getconn failed")

    fake_pool.AsyncConnectionPool = _BoomPool
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="getconn failed"):
        async with fake_pool.get_async_connection():
            pass
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    # logging.exception attaches the traceback; logging.error(e) would not.
    assert errors
    assert errors[0].exc_info is not None


async def test_pool_open_failure_closes_partial_pool(fake_pool):
    created = []

    class _FailOpenPool(_FakePool):
        def __init__(self, conninfo, **kwargs):
            super().__init__(conninfo, **kwargs)
            created.append(self)

        async def open(self):
            raise RuntimeError("open failed")

    fake_pool.AsyncConnectionPool = _FailOpenPool
    with pytest.raises(RuntimeError, match="open failed"):
        await fake_pool.get_connection_pool()
    # The partially-constructed pool is closed (workers not orphaned) and no
    # half-open pool is cached.
    assert created
    assert created[0].closed is True
    assert fake_pool._pool is None


async def test_cursor_yields_cursor(fake_pool):
    async with fake_pool.cursor() as cur:
        assert cur is not None

"""Unit tests for the CLI runner and the app lifespan: transport selection,
stdio/host-port validation, and pool open/close around the server run. The tool
loader, connection pool, and FastMCP server are stubbed.
"""

import pytest


@pytest.fixture
def runner_env(monkeypatch):
    import tai42_mcp_dynamic_postgres.cli.main as main_mod

    calls = {"run_async": None, "closed": 0}

    async def fake_load(**kwargs):
        return None

    class _FakeAcm:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    def fake_get_async_connection():
        return _FakeAcm()

    async def fake_run_async(transport=None, **kwargs):
        calls["run_async"] = {"transport": transport, **kwargs}

    async def fake_close():
        calls["closed"] += 1

    monkeypatch.setattr(main_mod, "load_dynamic_tools", fake_load)
    monkeypatch.setattr(main_mod, "get_async_connection", fake_get_async_connection)
    monkeypatch.setattr(main_mod.mcp_app, "run_async", fake_run_async)
    monkeypatch.setattr(main_mod, "close_connection_pool", fake_close)
    return main_mod, calls


async def test_runner_stdio_starts_server_and_closes_pool(runner_env):
    main_mod, calls = runner_env
    result = await main_mod.runner(
        overwrite=True,
        readonly=False,
        allow_unfiltered=False,
        select_joined=(),
        ignore_insert_column=("id",),
        ignore_select_column=(),
        ignore_update_column=("id",),
        ignore_select_joined_column=(),
        transport="stdio",
        host="127.0.0.1",
        port=8000,
    )
    assert result is None
    assert calls["run_async"]["transport"] == "stdio"
    assert calls["closed"] == 1


async def test_runner_stdio_rejects_host_port(runner_env):
    main_mod, _ = runner_env
    with pytest.raises(Exception, match="stdio"):
        await main_mod.runner(
            overwrite=True,
            readonly=False,
            allow_unfiltered=False,
            select_joined=(),
            ignore_insert_column=(),
            ignore_select_column=(),
            ignore_update_column=(),
            ignore_select_joined_column=(),
            transport="stdio",
            host="0.0.0.0",
            port=9000,
        )


async def test_runner_http_passes_host_port(runner_env):
    main_mod, calls = runner_env
    await main_mod.runner(
        overwrite=True,
        readonly=True,
        allow_unfiltered=False,
        select_joined=(),
        ignore_insert_column=(),
        ignore_select_column=(),
        ignore_update_column=(),
        ignore_select_joined_column=(),
        transport="http",
        host="127.0.0.1",
        port=9001,
    )
    assert calls["run_async"]["transport"] == "http"
    assert calls["run_async"]["host"] == "127.0.0.1"
    assert calls["run_async"]["port"] == 9001


async def test_app_lifespan_opens_and_closes_pool(monkeypatch):
    import tai42_mcp_dynamic_postgres.core.app as app_mod

    events = []

    async def fake_get():
        events.append("open")

    async def fake_close():
        events.append("close")

    monkeypatch.setattr(app_mod, "get_connection_pool", fake_get)
    monkeypatch.setattr(app_mod, "close_connection_pool", fake_close)

    async with app_mod.lifespan(app_mod.mcp_app):
        assert events == ["open"]
    assert events == ["open", "close"]

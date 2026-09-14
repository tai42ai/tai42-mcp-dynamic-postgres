"""Unit tests for psycopg type-adapter registration: the vector loader, the JSON
dumper safety net, and the combined loader registration. The connection's adapter
registry is stubbed.
"""

import warnings

import pytest


def test_vector_loader_parses_literal():
    from tai42_mcp_dynamic_postgres.database.type_adapters import VectorLoader

    loader = VectorLoader.__new__(VectorLoader)
    assert loader.load(b"[1.0,2.5,3.0]") == [1.0, 2.5, 3.0]
    with pytest.raises(ValueError, match="Invalid vector format"):
        loader.load(b"1,2,3")


class _FakeAdapters:
    def __init__(self):
        self.dumpers = []
        self.loaders = []

    def register_dumper(self, cls, dumper):
        self.dumpers.append((cls, dumper))

    def register_loader(self, oid, loader):
        self.loaders.append((oid, loader))


class _FakeAdaptConn:
    def __init__(self):
        self.adapters = _FakeAdapters()


def test_register_json_dumpers_registers_dict():
    from tai42_mcp_dynamic_postgres.database.type_adapters import register_json_dumpers

    conn = _FakeAdaptConn()
    register_json_dumpers(conn)
    assert any(cls is dict for cls, _ in conn.adapters.dumpers)


async def test_register_vector_warns_when_missing(monkeypatch):
    import tai42_mcp_dynamic_postgres.database.type_adapters as type_adapters_mod

    async def fake_fetch(conn, name):
        return None

    monkeypatch.setattr(type_adapters_mod.TypeInfo, "fetch", staticmethod(fake_fetch))
    with pytest.warns(RuntimeWarning, match="pgvector"):
        await type_adapters_mod.register_vector_as_list(_FakeAdaptConn())


async def test_register_types_loaders_runs(monkeypatch):
    import tai42_mcp_dynamic_postgres.database.type_adapters as type_adapters_mod

    async def fake_fetch(conn, name):
        return None

    monkeypatch.setattr(type_adapters_mod.TypeInfo, "fetch", staticmethod(fake_fetch))
    conn = _FakeAdaptConn()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        await type_adapters_mod.register_types_loaders(conn)
    assert any(cls is dict for cls, _ in conn.adapters.dumpers)

"""The authored tai-plugin.yml validates as a contract-less mcp-server spec, both
copies stay in sync, and its mcp.env markers name exactly the required and
defaulted connection vars.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml
from tai42_contract.plugins import PluginItemKind, PluginSpec
from tai42_kit.plugins import validate_docs
from tai42_kit.utils.data.env_markers import scan_env_marker_refs

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROOT_SPEC = _REPO_ROOT / "tai-plugin.yml"
_PACKAGED_SPEC = _REPO_ROOT / "src" / "tai42_mcp_dynamic_postgres" / "tai-plugin.yml"

# Per-deployment values that must be supplied (bare `${VAR}` markers) vs. those
# that carry the server's own defaults.
_REQUIRED_VARS = {"PG_DB", "PG_USER", "PG_PASSWORD"}
_DEFAULTED_VARS = {"PG_HOST", "PG_PORT"}


def _spec() -> PluginSpec:
    return PluginSpec.model_validate(yaml.safe_load(_ROOT_SPEC.read_text(encoding="utf-8")))


def _pyproject() -> dict:
    return tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_spec_is_a_contract_less_mcp_server() -> None:
    spec = _spec()
    assert spec.ref == "tai42/mcp-dynamic-postgres"
    # An mcp-server package imports no tai42-contract, so it declares no range.
    assert spec.contract is None
    kinds = [item.kind for item in spec.provides]
    assert kinds == [PluginItemKind.MCP_SERVER]


def test_spec_version_matches_the_package_version() -> None:
    # The spec version equals the built wheel's version, so a listing always
    # resolves to the wheel that carries these docs.
    assert _spec().version == _pyproject()["project"]["version"]


def test_mcp_transport_launches_the_console_script() -> None:
    mcp = _spec().provides[0].mcp
    assert mcp is not None
    assert mcp.command == "uvx"
    assert mcp.args == ["tai42-mcp-dynamic-postgres"]


def test_every_mcp_env_value_is_an_env_marker() -> None:
    # A typo dropping the `!ENV ` prefix would hand a literal string to the
    # subprocess instead of resolving from the environment — assert every value
    # is a marker that the shared scanner recognizes.
    mcp = _spec().provides[0].mcp
    assert mcp is not None
    refs = scan_env_marker_refs(mcp.env)
    scanned = {ref.var for ref in refs}
    assert scanned == _REQUIRED_VARS | _DEFAULTED_VARS
    for name, value in mcp.env.items():
        assert value.startswith("!ENV "), f"{name} is not an !ENV marker: {value!r}"


def test_required_and_defaulted_marker_sets() -> None:
    mcp = _spec().provides[0].mcp
    assert mcp is not None
    refs = scan_env_marker_refs(mcp.env)
    required = {ref.var for ref in refs if ref.required}
    defaulted = {ref.var for ref in refs if not ref.required}
    assert required == _REQUIRED_VARS
    assert defaulted == _DEFAULTED_VARS


def test_packaged_copy_is_identical_to_the_root_spec() -> None:
    assert _PACKAGED_SPEC.read_bytes() == _ROOT_SPEC.read_bytes()


def test_docs_and_spec_are_declared_in_package_data() -> None:
    package_data = _pyproject()["tool"]["setuptools"]["package-data"]["tai42_mcp_dynamic_postgres"]
    for pattern in ("tai-plugin.yml", "docs/*"):
        assert pattern in package_data, f"{pattern!r} must be shipped via package-data; got {package_data!r}"


def test_in_package_docs_satisfy_the_contract() -> None:
    docs_dir = _PACKAGED_SPEC.parent / "docs"
    files = {
        f"docs/{path.relative_to(docs_dir).as_posix()}": path.read_bytes()
        for path in docs_dir.rglob("*")
        if path.is_file()
    }
    # First-party plugin: full component subset allowed, but the shape, front
    # matter, and in-set references are still enforced.
    validate_docs(files, first_party=True)

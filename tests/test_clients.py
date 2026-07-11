"""Tests for per-naming-mode tool-name formats + preset loading (clients.py)."""
from mcpp.clients import (
    REGISTRY, CLIENT_ROUTES, get_format, presets,
    TemplateFormat, DefaultFormat,
)


def test_presets_load_and_have_required_sections():
    p = presets()
    assert "naming_modes" in p
    assert "connect_modes" in p
    assert "clients" in p
    # all six naming modes present
    for nid in ("claude", "cursor", "opencode", "coder", "codex", "default"):
        assert nid in p["naming_modes"]


def test_registry_built_from_presets():
    for nid in ("default", "claude", "cursor", "opencode", "coder", "codex"):
        assert nid in REGISTRY
    assert isinstance(REGISTRY["default"], DefaultFormat)
    assert isinstance(REGISTRY["claude"], TemplateFormat)


def test_routes_use_toolset_placeholder():
    # every naming mode (incl. default) embeds {toolset}; /mcp is a legacy alias
    # registered separately in main.py.
    assert CLIENT_ROUTES["default"] == "/{toolset}/default/mcp"
    assert CLIENT_ROUTES["claude"] == "/{toolset}/claude/mcp"
    assert CLIENT_ROUTES["codex"] == "/{toolset}/codex/mcp"


def test_get_format_falls_back_to_default():
    fmt = get_format("nonexistent")
    assert fmt.naming_id == "default"


def test_each_naming_mode_round_trips():
    cases = {
        "claude":   ("mcp__search__papers", "search", "papers"),
        "cursor":   ("mcp_search_papers",   "search", "papers"),
        "opencode": ("search_papers",       "search", "papers"),
        "coder":    ("search__papers",      "search", "papers"),
        "codex":    ("search::papers",      "search", "papers"),
    }
    for nid, (expected, server, tool) in cases.items():
        f = REGISTRY[nid]
        name = f.format_name(server, tool)
        assert name == expected, f"{nid}: {name}"
        assert f.parse_name(server, name) == tool
        # wrong server → None
        assert f.parse_name("other", name) is None


def test_default_passthrough():
    f = REGISTRY["default"]
    assert f.format_name("anything", "zhihu") == "zhihu"
    assert f.parse_name("anything", "zhihu") is None


def test_underscored_names_round_trip():
    """Server/tool names containing underscores survive round-trip.

    Non-greedy server + greedy tool splits at the first separator run.
    For claude (mcp__{server}__{tool}), my_search/do_thing works because
    the server group is non-greedy and stops at the first __.
    """
    f = REGISTRY["claude"]
    name = f.format_name("my_search", "do_thing")
    # my_search contains _ but not __, so the split is unambiguous
    assert name == "mcp__my_search__do_thing"
    assert f.parse_name("my_search", name) == "do_thing"


def test_clients_reference_valid_modes():
    """Every client in the preset points at a defined naming + connect mode."""
    p = presets()
    namings = set(p["naming_modes"]) | {"default"}
    connects = set(p["connect_modes"])
    for cname, spec in p["clients"].items():
        assert spec["naming"] in namings, f"{cname}: bad naming {spec['naming']}"
        assert spec["connect"] in connects, f"{cname}: bad connect {spec['connect']}"

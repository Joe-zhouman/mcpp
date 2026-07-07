"""Tests for per-client tool-name formats (clients.py)."""
from mcpp.clients import REGISTRY, get_format, ClaudeFormat, CursorFormat, DefaultFormat


def test_registry_has_three_clients():
    assert set(REGISTRY) >= {"default", "claude", "cursor"}
    assert isinstance(REGISTRY["claude"], ClaudeFormat)
    assert isinstance(REGISTRY["cursor"], CursorFormat)
    assert isinstance(REGISTRY["default"], DefaultFormat)


def test_get_format_falls_back_to_default():
    fmt = get_format("nonexistent")
    assert fmt.client_id == "default"


def test_claude_format_round_trip():
    f = REGISTRY["claude"]
    name = f.format_name("search", "zhihu")
    assert name == "mcp__search__zhihu"
    assert f.parse_name("search", name) == "zhihu"
    # wrong server prefix → no match
    assert f.parse_name("files", name) is None
    assert f.parse_name("search", "mcp__files__zhihu") is None


def test_cursor_format_round_trip():
    f = REGISTRY["cursor"]
    name = f.format_name("search", "zhihu")
    assert name == "mcp_search_zhihu"
    assert f.parse_name("search", name) == "zhihu"


def test_default_format_passthrough():
    f = REGISTRY["default"]
    assert f.format_name("anything", "zhihu") == "zhihu"
    assert f.parse_name("anything", "zhihu") is None


def test_claude_format_with_underscored_names():
    """Server/tool names containing underscores survive round-trip."""
    f = REGISTRY["claude"]
    name = f.format_name("my_search", "do_thing")
    assert name == "mcp__my_search__do_thing"
    assert f.parse_name("my_search", name) == "do_thing"

import pytest
from mcpp.transform import transform_tools, param_transform_value
from mcpp.upstream import Tool
from mcpp.config import Config

SAMPLE_RAW = """
upstreams:
  - name: gh
    url: https://api.github.com/mcp
expose:
  gh/search:
    upstream: gh
    tool: search
    as: gh_search
    description: "Search GitHub. Use `gh/code` for deep code search."
    params:
      - name: query
      - name: sort_order
        map_from: sort
      - name: per_page
        hidden: true
        default: 20
      - name: format
        default: json
  gh/code:
    upstream: gh
    tool: code_search
    description: "Deep code search. Call `gh/search` first for broad queries."
"""


@pytest.fixture
def sample_config():
    return Config.from_yaml(SAMPLE_RAW)


@pytest.fixture
def upstream_tools():
    return [
        Tool(name="search", description="Search repos",
             inputSchema={"type": "object", "properties": {
                 "query": {"type": "string", "description": "Search query"},
                 "sort": {"type": "string", "description": "Sort order"},
                 "per_page": {"type": "integer", "description": "Results per page"},
                 "format": {"type": "string", "description": "Output format"},
             }, "required": ["query"]}),
        Tool(name="code_search", description="Deep code search"),
        Tool(name="unused_tool", description="Not exposed"),
    ]


def test_hides_unexposed_tools(sample_config, upstream_tools):
    result = transform_tools("gh", upstream_tools, sample_config)
    names = [t.name for t in result]
    assert "search" not in names
    assert "gh_search" in names
    assert "code" in names          # key "gh/code", no as_ → display name "code"
    assert "unused_tool" not in names


def test_backtick_resolved_to_display_names(sample_config, upstream_tools):
    result = transform_tools("gh", upstream_tools, sample_config)
    by_name = {t.name: t for t in result}
    assert by_name["gh_search"].description == (
        "Search GitHub. Use `code` for deep code search."
    )


def test_hidden_params_removed_from_schema(sample_config, upstream_tools):
    result = transform_tools("gh", upstream_tools, sample_config)
    by_name = {t.name: t for t in result}
    props = by_name["gh_search"].inputSchema["properties"]
    assert "query" in props
    assert "sort_order" in props
    assert "per_page" not in props  # hidden


def test_optional_params_with_default_not_in_required(sample_config, upstream_tools):
    result = transform_tools("gh", upstream_tools, sample_config)
    by_name = {t.name: t for t in result}
    required = by_name["gh_search"].inputSchema.get("required", [])
    assert "query" in required      # no default → required
    assert "format" not in required # has default → optional


def test_upstream_param_descriptions_preserved(sample_config, upstream_tools):
    result = transform_tools("gh", upstream_tools, sample_config)
    by_name = {t.name: t for t in result}
    props = by_name["gh_search"].inputSchema["properties"]
    assert props["query"]["description"] == "Search query"


def test_param_transform_hidden_default():
    cfg = Config.from_yaml(SAMPLE_RAW)
    entry = cfg.expose["gh/search"]
    result = param_transform_value(
        {"query": "test", "sort_order": "stars"},
        entry,
    )
    assert result["query"] == "test"
    assert result["sort"] == "stars"
    assert result["per_page"] == 20    # hidden default injected


def test_param_transform_value_enum_and_preset():
    yaml = """
upstreams:
  - name: ai
    url: https://ai.example.com/mcp
expose:
  ai/chat:
    upstream: ai
    tool: chat
    params:
      - name: message
      - name: style
        map_from: temperature
        type: enum
        mapping:
          precise: 0.1
          balanced: 1.0
      - name: mode
        type: preset
        preset:
          fast: {"model": "haiku", "max_tokens": 4096}
          deep: {"model": "opus", "max_tokens": 256000}
"""
    cfg = Config.from_yaml(yaml)
    entry = cfg.expose["ai/chat"]
    result = param_transform_value({"message": "hi", "style": "precise", "mode": "deep"}, entry)
    assert result["message"] == "hi"
    assert result["temperature"] == 0.1
    assert result["model"] == "opus"
    assert result["max_tokens"] == 256000

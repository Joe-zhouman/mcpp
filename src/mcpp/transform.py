from __future__ import annotations

from typing import Optional, Any

from mcpp.config import Config, ExposeEntry, BACKTICK_REF
from mcpp.upstream import Tool


def _build_display_name_map(config: Config) -> dict[str, str]:
    """Map stable key -> display name (as_ or last segment of key)."""
    return {
        key: entry.display_name(key)
        for key, entry in config.expose.items()
    }


def _resolve_backticks(description: str, display_map: dict[str, str]) -> str:
    """Replace `key` backtick refs with display names."""

    def repl(m):
        ref = m.group(1)
        return f"`{display_map.get(ref, ref)}`"

    return BACKTICK_REF.sub(repl, description)


def _build_downstream_schema(
    entry: ExposeEntry,
    upstream_schema: Optional[dict] = None,
) -> Optional[dict]:
    """Build downstream inputSchema from param transforms.

    Params with `default` are optional (not in required list).
    Upstream param descriptions preserved unless overridden.
    Returns None if entry.params is None (passthrough upstream schema).
    """
    if entry.params is None:
        return upstream_schema

    upstream_props: dict[str, Any] = (upstream_schema or {}).get("properties", {})
    properties: dict[str, dict] = {}
    required: list[str] = []

    for p in entry.params:
        if p.hidden:
            continue
        upstream_name = p.map_from or p.name
        upstream_param = upstream_props.get(upstream_name, {})
        prop: dict = {"type": upstream_param.get("type", "string")}
        desc = upstream_param.get("description", "")
        if desc:
            prop["description"] = desc
        if p.type == "enum" and p.mapping:
            prop["type"] = "string"
            prop["enum"] = list(p.mapping.keys())
        elif p.type == "preset" and p.preset:
            prop["type"] = "string"
            prop["enum"] = list(p.preset.keys())
        properties[p.name] = prop
        if p.default is None:
            required.append(p.name)

    schema: dict = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def param_transform_value(
    raw_args: dict,
    entry: ExposeEntry,
) -> dict:
    """Transform downstream arguments back to upstream format."""
    if entry.params is None:
        return raw_args

    upstream: dict[str, Any] = {}
    for p in entry.params:
        upstream_name = p.map_from or p.name
        if p.hidden:
            if p.default is not None:
                upstream[upstream_name] = p.default
            continue
        value = raw_args.get(p.name)
        if value is None:
            continue
        if p.type == "enum" and p.mapping:
            if value in p.mapping:
                upstream[upstream_name] = p.mapping[value]
            else:
                raise ValueError(
                    f"Invalid value '{value}' for enum param '{p.name}'. "
                    f"Valid: {list(p.mapping.keys())}"
                )
        elif p.type == "preset" and p.preset:
            if value in p.preset:
                upstream |= p.preset[value]
            else:
                raise ValueError(
                    f"Invalid value '{value}' for preset param '{p.name}'. "
                    f"Valid: {list(p.preset.keys())}"
                )
        else:
            upstream[upstream_name] = value
    return upstream


def transform_tools(
    upstream_name: str,
    tools: list[Tool],
    config: Config,
) -> list[Tool]:
    """Transform upstream tool list to downstream tool surface."""
    display_map = _build_display_name_map(config)
    result: list[Tool] = []

    # Build reverse lookup: (upstream, tool) -> (key, entry)
    by_upstream_tool: dict[tuple[str, str], tuple[str, ExposeEntry]] = {
        (e.upstream, e.tool): (key, e)
        for key, e in config.expose.items()
    }

    for tool in tools:
        lookup = by_upstream_tool.get((upstream_name, tool.name))
        if lookup is None:
            continue
        key, entry = lookup
        if entry.hide:
            continue

        display_name = display_map.get(key, tool.name)
        description = entry.description or tool.description or ""
        description = _resolve_backticks(description, display_map)
        input_schema = _build_downstream_schema(entry, tool.inputSchema)

        result.append(Tool(
            name=display_name,
            description=description,
            inputSchema=input_schema,
        ))

    return result

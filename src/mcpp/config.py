from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel

BACKTICK_REF = re.compile(r"`(\S+)`")


class AuthConfig(BaseModel):
    keys: list[str]


class UpstreamConfig(BaseModel):
    name: str
    url: str
    auth: Optional[AuthConfig] = None
    connect_timeout: int = 30
    read_timeout: int = 120


class ParamTransform(BaseModel):
    name: str
    map_from: Optional[str] = None
    type: Optional[str] = None           # "enum" | "preset" | None
    mapping: Optional[dict[str, Any]] = None  # enum: val -> val
    preset: Optional[dict[str, dict[str, Any]]] = None  # preset name -> upstream params
    hidden: bool = False
    default: Any = None


class ExposeEntry(BaseModel):
    upstream: str        # upstream name
    tool: str            # upstream tool name
    as_: Optional[str] = None  # display name (key is the stable ref)
    hide: bool = False
    description: Optional[str] = None
    params: Optional[list[ParamTransform]] = None


class Config(BaseModel):
    upstreams: list[UpstreamConfig]
    expose: dict[str, ExposeEntry]  # key = "upstream/tool"

    @classmethod
    def from_yaml(cls, content: str) -> "Config":
        raw = yaml.safe_load(content)
        cfg = cls.model_validate(raw)
        cfg.validate_refs()
        return cfg

    def validate_refs(self) -> "Config":
        """Validate backtick cross-tool references in descriptions.
        Each `key` must refer to an existing, non-hidden exposed tool.
        Raises ValueError with details on first invalid ref.
        """
        for key, entry in self.expose.items():
            if not entry.description:
                continue
            for ref in BACKTICK_REF.findall(entry.description):
                if ref not in self.expose:
                    raise ValueError(
                        f"Tool '{key}' description references '{ref}', "
                        f"which does not exist in expose"
                    )
                if self.expose[ref].hide:
                    raise ValueError(
                        f"Tool '{key}' description references '{ref}', "
                        f"which is hidden (hide: true)"
                    )
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        return cls.from_yaml(Path(path).read_text())

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(exclude_none=True, mode="json"),
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

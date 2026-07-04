from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel


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
        return cls.model_validate(raw)

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

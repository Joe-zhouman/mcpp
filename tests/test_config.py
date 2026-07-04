import pytest
from mcpp.config import Config, UpstreamConfig, AuthConfig, ExposeEntry


def test_minimal_config():
    yaml = """
upstreams:
  - name: gh
    url: https://api.github.com/mcp
expose: {}
"""
    cfg = Config.from_yaml(yaml)
    assert len(cfg.upstreams) == 1
    assert cfg.upstreams[0].name == "gh"
    assert cfg.upstreams[0].url == "https://api.github.com/mcp"
    assert cfg.upstreams[0].auth is None
    assert cfg.expose == {}


def test_auth_config():
    yaml = """
upstreams:
  - name: gh
    url: https://api.github.com/mcp
    auth:
      keys:
        - ${GH_KEY_1}
        - ${GH_KEY_2}
expose: {}
"""
    cfg = Config.from_yaml(yaml)
    assert cfg.upstreams[0].auth is not None
    assert cfg.upstreams[0].auth.keys == ["${GH_KEY_1}", "${GH_KEY_2}"]

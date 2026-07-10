#!/usr/bin/env bash
# mcpp 一键启动脚本
# 用法:
#   ./start.sh              # 默认 0.0.0.0:9020,前台运行,Ctrl+C 停止
#   ./start.sh 8080         # 指定端口
#   MCPP_HOST=127.0.0.1 ./start.sh   # 仅本机访问
#   TOKEN=yes ./start.sh    # 生成随机 token 并开启鉴权(跨主机推荐)
set -euo pipefail

cd "$(dirname "$0")"

# --- 找 python:优先用 venv,否则用系统 python3 ---
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "✗ 找不到 python3,请先安装 Python >=3.12" >&2
  exit 1
fi

# --- 依赖检查:缺了自动 pip install ---
if ! "$PY" -c "import fastapi, uvicorn, httpx, yaml, pydantic" >/dev/null 2>&1; then
  echo "⚠ 缺少依赖,正在安装...(使用 $([ -d .venv ] && echo .venv || echo '系统 python'))"
  if [ ! -d .venv ]; then
    echo "  建议先建虚拟环境: python3 -m venv .venv && .venv/bin/pip install -e ."
  fi
  "$PY" -m pip install -e . -q
fi

# --- 配置文件:没有 config.yaml 就从 example 复制 ---
if [ ! -f config.yaml ]; then
  echo "⚠ 未找到 config.yaml,从 config.yaml.example 复制一份..."
  cp config.yaml.example config.yaml
  echo "  已生成 config.yaml,请按需编辑 upstreams/expose 后重新运行。"
fi

# --- 端口 / host ---
PORT="${1:-${MCPP_PORT:-9020}}"
export MCPP_PORT="$PORT"
export MCPP_HOST="${MCPP_HOST:-0.0.0.0}"
export MCPP_CONFIG="${MCPP_CONFIG:-config.yaml}"

# --- 可选鉴权:TOKEN=yes 时生成随机 token 写进临时 config ---
AUTH_NOTE="未开启鉴权(开放)"
if [ "${TOKEN:-}" = "yes" ]; then
  TOKEN_VAL="$("$PY" -c "import secrets; print(secrets.token_urlsafe(16))")"
  # 把 auth.token 注入 config.yaml(若已有 auth 段则跳过,避免重复)
  if ! grep -q "^auth:" config.yaml; then
    printf '\nauth:\n  token: %s\n' "$TOKEN_VAL" >> config.yaml
  else
    AUTH_NOTE="已开启鉴权,但 config.yaml 已有 auth 段(沿用旧 token)"
    TOKEN_VAL="(见 config.yaml)"
  fi
  AUTH_NOTE="已开启鉴权 token=$TOKEN_VAL"
fi

# --- 计算访问地址 ---
LOCAL_URL="http://127.0.0.1:${PORT}/admin"
LAN_IP="$("$PY" -c "
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('8.8.8.8', 80))
    print(s.getsockname()[0])
    s.close()
except Exception:
    print('')
" 2>/dev/null || true)"

cat <<EOF

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  mcpp 已启动
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  本机访问:   ${LOCAL_URL}
EOF
if [ -n "$LAN_IP" ]; then
  echo "  局域网访问: http://${LAN_IP}:${PORT}/admin"
  [ "${TOKEN:-}" = "yes" ] && [ "$AUTH_NOTE" != "已开启鉴权,但 config.yaml 已有 auth 段(沿用旧 token)" ] \
    && echo "              (带 token: http://${LAN_IP}:${PORT}/admin?token=${TOKEN_VAL})"
  echo
  echo "  客户端 endpoint(Claude Code 等):"
  echo "    http://${LAN_IP}:${PORT}/<toolset>/claude/mcp"
fi
echo "  鉴权: ${AUTH_NOTE}"
echo "  日志: 实时输出在下方,Ctrl+C 停止"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exec "$PY" -c "from mcpp.main import run; run()"

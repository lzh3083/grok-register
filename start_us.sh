#!/bin/sh
# 启动脚本：美国住宅 IP 环境下的 grok-register
#
# 作用：
#   1. 常驻 Xvfb（项目默认 cpa_headless=false，需要图形环境）
#   2. 启动辣椒HTTP 代理订阅服务（本地 8899）
#   3. 启动 WebUI（本地 8092）
#
# 用法：
#   ./start_us.sh            启动全部
#   ./start_us.sh check      只做环境自检（不启动服务）
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
DISPLAY_NUM=":99"
XVFB_SCREEN="1440x900x24"

log() { printf '\033[36m[启动]\033[0m %s\n' "$1"; }
warn() { printf '\033[33m[注意]\033[0m %s\n' "$1"; }
err() { printf '\033[31m[错误]\033[0m %s\n' "$1"; }

# ---- 环境自检 ----
check_env() {
  log "检查虚拟环境..."
  if [ ! -x "$PY" ]; then
    err "未找到 $PY，请先创建虚拟环境：python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
    exit 1
  fi

  log "检查浏览器..."
  "$PY" - <<'EOF'
import sys
sys.path.insert(0, '.')
import us_consistency
path = us_consistency.detect_browser_path()
if not path:
    print('找不到 Chromium 可执行文件，请在 config.json 设置 browser_path')
    sys.exit(1)
print('浏览器:', path)
EOF

  log "检查配置..."
  "$PY" - <<'EOF'
import json, app_config
raw = json.load(open('config.json'))
cfg = app_config.validate_config_structure(raw)
print('配置校验通过；时区=%s 语言=%s' % (
    cfg['us_consistency_timezone'], cfg['us_consistency_locale']))
EOF
}

# ---- Xvfb ----
start_xvfb() {
  if pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null 2>&1; then
    log "Xvfb $DISPLAY_NUM 已在运行"
  else
    log "启动 Xvfb $DISPLAY_NUM"
    Xvfb "$DISPLAY_NUM" -screen 0 "$XVFB_SCREEN" >/tmp/xvfb.log 2>&1 &
    sleep 2
  fi
  export DISPLAY="$DISPLAY_NUM"
}

# ---- 代理订阅服务 ----
start_proxy_service() {
  if pgrep -f "lajiao_proxy.py.*serve" >/dev/null 2>&1; then
    log "代理订阅服务已在运行"
  else
    log "启动辣椒HTTP 代理订阅服务 (127.0.0.1:8899)"
    nohup "$PY" lajiao_proxy.py --num 8 serve --port 8899 >/tmp/lajiao.log 2>&1 &
    sleep 4
  fi
  if curl -s --max-time 5 http://127.0.0.1:8899/health >/dev/null 2>&1; then
    log "代理服务健康检查通过"
    curl -s http://127.0.0.1:8899/health
    echo
  else
    warn "代理服务健康检查失败，请查看 /tmp/lajiao.log"
  fi
}

# ---- WebUI ----
start_webui() {
  log "启动 WebUI (http://127.0.0.1:8092)"
  warn "WebUI 无鉴权且会明文显示代理凭据，切勿暴露到公网"
  export DISPLAY="$DISPLAY_NUM"
  exec "$PY" -m web.server
}

case "${1:-start}" in
  check)
    check_env
    log "环境自检完成"
    ;;
  start|"")
    check_env
    start_xvfb
    start_proxy_service
    start_webui
    ;;
  *)
    echo "用法: $0 [start|check]"
    exit 1
    ;;
esac

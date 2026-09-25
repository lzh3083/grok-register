#!/usr/bin/env bash
# 就绪自检：确认跑批前的每个环节都正常。
#
# 用法：
#   ./ready_check.sh            # 全部检查
#   ./ready_check.sh --no-browser   # 跳过浏览器启动（更快）
#
# 退出码：0 全部就绪；1 有阻塞项。
set -uo pipefail

cd "$(dirname "$0")" || exit 1
PY="./.venv/bin/python"
SKIP_BROWSER=0
[ "${1:-}" = "--no-browser" ] && SKIP_BROWSER=1

pass=0; fail=0; warn=0
ok()   { echo "  ✅ $1"; pass=$((pass+1)); }
bad()  { echo "  ❌ $1"; fail=$((fail+1)); }
warn() { echo "  ⚠️  $1"; warn=$((warn+1)); }

echo "════════════ 1. 配置 ════════════"
if $PY -c "
import json, app_config
cfg = app_config.validate_config(json.load(open('config.json')))
app_config.validate_run_requirements(cfg)
" 2>/dev/null; then
  ok "配置校验通过"
else
  bad "配置校验失败"; $PY -c "
import json, app_config
cfg = app_config.validate_config(json.load(open('config.json')))
app_config.validate_run_requirements(cfg)
" 2>&1 | tail -3 | sed 's/^/     /'
fi

echo
echo "════════════ 2. 临时邮箱 ════════════"
MAIL_OUT=$($PY -c "
import json, sys
sys.path.insert(0, '.')
import app_config, mail_service, browser_runtime
cfg = app_config.validate_config(json.load(open('config.json')))
mail_service.bind_runtime({'config': cfg, 'http_get': browser_runtime.http_get, 'http_post': browser_runtime.http_post})
addr, jwt = mail_service.get_email_and_token()
print('ADDR=' + addr)
msgs = mail_service.cloudflare_get_messages(cfg['cloudflare_api_base'], jwt)
print('COUNT=%d' % len(msgs))
" 2>&1)
if echo "$MAIL_OUT" | grep -q "^ADDR="; then
  ok "邮箱创建成功: $(echo "$MAIL_OUT" | grep '^ADDR=' | cut -d= -f2-)"
  ok "邮件读取正常（$(echo "$MAIL_OUT" | grep '^COUNT=' | cut -d= -f2) 封）"
else
  bad "邮箱链路失败"; echo "$MAIL_OUT" | tail -3 | sed 's/^/     /'
fi

echo
echo "════════════ 3. 浏览器与指纹 ════════════"
BP=$($PY -c "
import json, sys
sys.path.insert(0,'.')
import app_config, us_consistency
cfg = app_config.validate_config(json.load(open('config.json')))
us_consistency.configure(cfg)
print(us_consistency.detect_browser_path())
" 2>/dev/null)
if [ -n "$BP" ] && [ -x "$BP" ]; then
  ok "浏览器: $BP"
else
  bad "浏览器未找到（检查 browser_path 配置）"
fi

if [ "$SKIP_BROWSER" -eq 0 ]; then
  # 找一个可用的 X 显示号
  DISP=""
  for n in 99 97 98 100 101; do
    [ -e "/tmp/.X11-unix/X$n" ] && { DISP=":$n"; break; }
  done
  if [ -z "$DISP" ]; then
    rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null
    Xvfb :99 -screen 0 1440x900x24 >/tmp/xvfb_ready.log 2>&1 &
    sleep 3
    DISP=":99"
  fi
  FP=$(DISPLAY=$DISP timeout 180 $PY -c "
import json, sys, time, os
sys.path.insert(0,'.')
import app_config
import grok_register_ttk as app
app.config = app_config.validate_config(json.load(open('config.json')))
app._browser_runtime.configure_runtime(app.config)
app._mail_service.bind_runtime(app.__dict__)
app._registration_browser.bind_runtime(app.__dict__)
browser, page = app.start_browser(use_proxy=False)
page.get('https://example.com'); time.sleep(2)
print(page.run_js('''return JSON.stringify({
  tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
  platform: navigator.platform, langs: navigator.languages,
  cores: navigator.hardwareConcurrency, mem: navigator.deviceMemory,
  wd: navigator.webdriver, chrome: typeof window.chrome})'''))
browser.quit()
" 2>/dev/null | tail -1)
  if echo "$FP" | grep -q '"platform":"Win32"'; then
    ok "指纹一致: $FP"
  elif [ -n "$FP" ]; then
    warn "指纹异常: $FP"
  else
    bad "浏览器启动失败（DISPLAY=$DISP）"
  fi
else
  warn "已跳过浏览器启动检查"
fi

echo
echo "════════════ 4. 代理 ════════════"
NODEFILE=$( $PY -c "
import json
print(json.load(open('config.json')).get('proxy_pool_file',''))
" 2>/dev/null)
SUB=$( $PY -c "
import json
print(json.load(open('config.json')).get('proxy_pool_subscription_url',''))
" 2>/dev/null)
EXPECT=$( $PY -c "
import json
print(json.load(open('config.json')).get('mooproxy_country','US'))
" 2>/dev/null)
if [ -n "$NODEFILE" ] && [ -f "$NODEFILE" ]; then
  COUNT=$(grep -c . "$NODEFILE" 2>/dev/null || echo 0)
  if [ "$COUNT" -gt 0 ]; then
    ok "节点文件 $NODEFILE（$COUNT 个）"
    # 逐个校验出口国家：住宅代理可能混入非目标国家节点，必须实测而非相信接口。
    BAD=0; CHECKED=0
    while IFS= read -r node; do
      [ -z "$node" ] && continue
      CHECKED=$((CHECKED+1))
      RES=$(timeout 90 $PY mooproxy_bridge.py check --node "$node" --expect "$EXPECT" 2>&1 | tail -1 | sed 's/\[mooproxy\] //')
      if echo "$RES" | grep -q "✅"; then
        echo "     $RES"
      else
        echo "     ❌ $RES"
        BAD=$((BAD+1))
      fi
    done < "$NODEFILE"
    if [ "$BAD" -eq 0 ]; then
      ok "全部 $CHECKED 个节点出口均为 $EXPECT 住宅 IP"
    else
      bad "$BAD/$CHECKED 个节点不合格，建议重新生成"
    fi
  else
    bad "节点文件为空: $NODEFILE"
  fi
elif [ -n "$SUB" ]; then
  PROX=$(curl -s -m 30 "$SUB" 2>/dev/null)
  if echo "$PROX" | grep -qE '^[0-9a-zA-Z._-]+:[0-9]+'; then
    ok "代理可用: $(echo "$PROX" | head -1)"
  else
    bad "代理不可用: $(echo "$PROX" | head -2 | tr '\n' ' ' | cut -c1-160)"
  fi
else
  warn "未配置代理（proxy_pool_file 与 proxy_pool_subscription_url 均为空）"
fi

echo
echo "════════════ 5. CPA ════════════"
$PY -c "
import json
c = json.load(open('config.json'))
print('  CPA 导出:', '已启用' if c.get('cpa_export_enabled') else '未启用')
" 2>/dev/null
if [ -d cpa_auths ]; then
  ok "输出目录存在: $(realpath cpa_auths)"
else
  warn "cpa_auths 目录不存在（跑批时会自动创建）"
fi

echo
echo "════════════════════════════════"
echo "  通过 $pass | 警告 $warn | 失败 $fail"
echo "════════════════════════════════"
[ "$fail" -eq 0 ] && echo "✅ 环境就绪" || echo "❌ 存在阻塞项，请先解决"
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)

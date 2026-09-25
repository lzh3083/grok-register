# 美国住宅 IP 部署说明（本仓库改动）

> 基于上游 `AaronL725/grok-register`，为"美国动态住宅代理"做的环境一致性改造。

## 一、为什么要改造

上游项目不做任何浏览器指纹归一化。实测在无图形界面的 Linux 容器中暴露出**三方矛盾**：

| 项目 | 实测值 | 问题 |
|---|---|---|
| 浏览器时区 | `UTC` | 与任何目标国家都不符 |
| `navigator.platform` | `Linux x86_64` | 与 UA 声称的 `Windows NT 10.0` 直接矛盾 |
| `navigator.languages` | `["en-US"]` | 尚可，但未与 IP 国家联动 |

这类自相矛盾本身就是风控特征。**住宅 IP 的价值是"像真实用户"，指纹矛盾会直接抵消这个价值** ——
花在住宅代理上的钱可能白费。

## 二、改动清单

### 新增文件

| 文件 | 作用 |
|---|---|
| `us_consistency.py` | 环境一致性核心：时区/locale/platform/Client Hints 归一化 + 浏览器路径探测 |
| `us_consistency_check.py` | 出口国家 ↔ 时区/语言 一致性校验器 |
| `lajiao_proxy.py` | 辣椒HTTP 美国动态住宅代理适配层（提取 / 校验 / 本地订阅服务） |
| `start_us.sh` | 一键启动：Xvfb + 代理服务 + WebUI |
| `config.json` | 已按美国住宅场景配置好 |

### 修改的上游文件（每处仅少量插入）

| 文件 | 改动 |
|---|---|
| `app_config.py` | 新增 `us_consistency_*` 与 `lajiao_*` 配置项 |
| `browser_runtime.py` | `create_browser_options()` 末尾注入一致性参数（含 `--no-sandbox` 等容器必需项） |
| `registration_browser.py` | `start_browser()` 拿到 page 后注入 navigator 覆盖脚本 |

> 升级上游时，只需重新应用这三处插入即可。

## 三、修复前后对比（实测）

```
修复前：
  时区     = UTC                    ← 与 IP 国家矛盾
  platform = Linux x86_64           ← 与 UA 声称的 Windows 矛盾
  webdriver= False

修复后：
  时区     = America/New_York       ✅
  偏移     = 240 分钟（纽约正确值）  ✅
  platform = Win32                  ✅ 与 UA 自洽
  language = en-US                  ✅
  languages= ["en-US","en"]         ✅
  cores    = 8                      ✅ 桌面级配置
  devMemory= 8                      ✅
  chrome   = object                 ✅ 补齐，避免 headless 特征
```

## 四、关键设计决策

### 1. 时区用进程级 `TZ`，不用 CDP

`Emulation.setTimezoneOverride` 只对已建立的 target 生效，新开的 tab 需要重新注入，
容易漏。而 Chromium 在 Linux 上读取 `TZ` 环境变量，进程级设置后**所有子进程/新 tab 自动继承**，更可靠。

### 2. platform 用 JS 注入，不用 `setUserAgentOverride`

实测 CDP 的 `setUserAgentOverride(platform=...)` 会**顺带把 `navigator.languages`
污染成 `["en-US","en;q=0.9"]`** —— 这不是合法的语言标签格式，反而制造新异常。
改用 `Page.addScriptToEvaluateOnNewDocument` 注入 JS getter，干净可控。

### 3. 辣椒粘性时长必须 ≥ 60 分钟

一个账号从打开注册页 → 拿 SSO → 跑 CPA 导出，可能超过 10 分钟。
辣椒 `t` 参数是**粘性会话分钟数**，若短于单账号流程耗时，IP 会在注册中途变化 ——
这会直接触发风控。故默认 `t=60`。

### 4. 入池前校验住宅真实性

`lajiao_proxy.py` 对每个提取到的节点探测 `hosting` 字段，剔除机房 IP，
并校验国家必须是 `US`。避免脏 IP 进入注册流程。

## 五、使用步骤

### 前置 A：配置临时邮箱（Cloudflare Temp Email）

若你自建了 `cloudflare_temp_email`（本项目支持），需要区分两种认证模式：

| 模式 | 适用场景 | 配置 |
|---|---|---|
| `x-admin-auth` | 有管理员密码，走 `/admin/new_address` | `cloudflare_api_key` = admin 密码 |
| `x-user-token` | **无 admin 密码**，普通账号自助登录 | `cloudflare_api_key` = `邮箱:密码` |

**推荐 `x-user-token`**：实例即使开启了「禁止匿名创建邮箱」
（`disableAnonymousUserCreateEmail: true`），普通用户仍可自助注册并建址，
不需要向管理员索要密码。

```jsonc
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://apimail.example.com",  // API 域名，常与网页域名不同
  "cloudflare_auth_mode": "x-user-token",
  "cloudflare_api_key": "你的账号@你的域名:你的密码",     // 首次会自动注册
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_messages": "/api/mails",
  "cloudflare_path_domains": "/api/domains",
  "defaultDomains": "你的域名"
}
```

> ⚠️ **API 域名可能与网页域名不同**。例如网页在 `mail.example.com`，
> 而 API 在 `apimail.example.com`。`cloudflare_api_base` 必须填 **API 域名**。
> 判断方法：网页源码里搜 `apiBase`，或直接试 `/open_api/settings` 能否返回 JSON。

### 前置 B：配置辣椒白名单

未加白名单时，API 会直接返回错误文本：

```
<你的出口IP> not added to whitelist
```

**这是白名单认证模式**，需要二选一：

- **方案 A**：登录辣椒后台，把本机出口 IP 加入 API 白名单
- **方案 B**：用 `--via` 指定一个已加白名单的固定出口

⚠️ **注意：多出口主机的源 IP 会漂移**。实测同一台机器访问不同探测服务时，
源 IP 可能不同（例如访问 A 服务走 IP1，访问 B 服务走 IP2）。
这意味着即使把当前 IP 加进白名单，换个目标就可能失效。

**建议用方案 B 固定出口**，否则白名单会随机失效。

### 步骤 1：自检

```bash
cd <项目目录>
./start_us.sh check
```

### 步骤 2：验证代理可用性与一致性

```bash
# 提取并校验节点
./.venv/bin/python lajiao_proxy.py --num 2 extract --check

# 校验出口国家与时区是否自洽
./.venv/bin/python us_consistency_check.py \
    --proxy "http://<提取到的节点>" \
    --timezone America/New_York --expect US
```

期望输出：

```
✅ 校验结果: OK
   - 一致：出口 US（Comcast Cable...），时区 America/New_York，语言 en-US
```

### 步骤 3：启动

```bash
./start_us.sh
```

然后访问 `http://127.0.0.1:8092`。

## 六、配置说明

```jsonc
{
  // 环境一致性
  "us_consistency_enabled": true,
  "us_consistency_timezone": "America/New_York",  // 美国东部
  "us_consistency_locale": "en-US",
  "browser_path": "",               // 留空自动探测；容器内建议显式指定

  // 辣椒代理
  "lajiao_api_base": "http://api.lajiaohttp.com/api/extract_ip",
  "lajiao_regions": "US",
  "lajiao_num": 8,                  // 一次提取节点数
  "lajiao_sticky_minutes": 60,      // 粘性时长，必须 ≥ 单账号耗时
  "lajiao_extract_via": "",         // 固定白名单源 IP 用的上游代理
  "lajiao_require_residential": true,
  "lajiao_require_country": "US",

  // 代理池：订阅指向本地服务
  "proxy_mode": "pool",
  "proxy_pool_subscription_url": "http://127.0.0.1:8899/proxies",
  "proxy_pool_max_concurrent_per_node": 1,   // 一号一 IP，避免关联
  "proxy_protocol_backend": "native-only"    // 住宅代理是 HTTP，不需要 sing-box
}
```

## 七、踩坑记录

### 坑 1：`socks5://` vs `socks5h://`（重要）

本机 DNS 被 **fake-ip 劫持**：

```
api.ipify.org  -> 198.18.0.57      ← Clash/Mihomo fake-ip 网段
accounts.x.ai  -> 198.18.11.118
```

`socks5://` 会**本地解析 DNS**，拿到假 IP 后连接必然失败（实测桥返回 502）。
必须用 `socks5h://` 让**远端解析**。

> 住宅代理是 HTTP 协议，不受此影响。但若用 SSH 隧道做固定出口，务必用 `socks5h://`。

### 坑 2：容器内浏览器必需参数

DrissionPage 默认参数在容器内会启动失败：

- `--no-sandbox`：容器通常无特权
- `--disable-dev-shm-usage`：`/dev/shm` 偏小

已在 `us_consistency.apply_browser_options()` 中统一加上。

### 坑 3：浏览器路径要显式指定

容器内 `HOME` 与浏览器实际安装位置常常不一致（浏览器可能装在持久化数据目录），
DrissionPage 找不到会报 `Browser not found`。

`us_consistency.detect_browser_path()` 按以下顺序自动探测：

1. 配置项 `browser_path`
2. 环境变量 `GROK_BROWSER_PATH`（直接给出可执行文件）
3. 环境变量 `PLAYWRIGHT_BROWSERS_PATH`（给出根目录，自动找 `chromium-*/chrome-linux64/chrome`）
4. 常见系统安装位置（`/usr/bin/google-chrome`、`/usr/bin/chromium` 等）

容器内建议直接在配置里写死 `browser_path`，最省事。

### 坑 4：PEP 668

系统 Python 禁止直接 pip 安装，必须用 venv（项目 README 也是这么建议的）。

## 八、待办 / 注意事项

- [ ] **把出口 IP 加入辣椒白名单**（或用 `--via` 固定出口），否则提取接口一直返回白名单错误
- [ ] 首次跑批前，先用 `us_consistency_check.py` 确认拿到的是**美国住宅 IP**（`hosting=false`）
- [ ] 小规模验证（1 个账号）确认成功率后，再放量
- [ ] `register_count` 与 `lajiao_num` 要匹配：每个账号独占一个 IP

## 九、风险提示

1. **合规风险**：批量注册账号通常违反 xAI 服务条款，账号随时可能被封禁。
   本改造只提高"环境自洽性"，不改变这一根本风险。
2. **成本**：住宅代理按流量计费。注册流程会加载大量页面资源，
   建议先小规模测通再放量。
3. **WebUI 无鉴权**：会明文显示代理凭据，仅可本机访问，切勿暴露公网。
4. **一号一 IP**：多个账号共用一个住宅 IP 是明显的关联特征，
   `proxy_pool_max_concurrent_per_node=1` 必须保持。

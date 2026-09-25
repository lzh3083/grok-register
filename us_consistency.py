"""美国住宅 IP 环境一致性模块。

背景
----
grok-register 原版不做任何浏览器指纹归一化，实测在容器/服务器上会出现三方矛盾：

    IP 国家:  US（美国住宅代理）
    浏览器时区: UTC            <- 与 IP 矛盾
    navigator.platform: Linux x86_64  <- 与 UA 声称的 Windows 矛盾

这类矛盾本身就是风控特征，会抵消住宅 IP 带来的"真实用户"收益。
本模块把时区、语言、platform、CPU/内存等指纹统一到"美国 Windows 桌面用户"。

设计原则
--------
1. 只做一致性，不做伪装强度竞赛：目标是消除自相矛盾，而非对抗专业指纹库。
2. 最小侵入：原项目文件只加一行调用，升级时冲突面最小。
3. 可关闭：配置 us_consistency_enabled=false 即完全退回原版行为。
"""

from __future__ import annotations

import os
import time

# 美国东部时区：人口最密集，是"美国用户"最自然的默认值。
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_LOCALE = "en-US"

# 与 UA 保持一致的 Windows 桌面参数。
DEFAULT_PLATFORM = "Win32"
DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9"
DEFAULT_CORES = 8
DEFAULT_DEVICE_MEMORY = 8

# Client Hints：UA 声称 Windows + Chrome，这些头必须自洽，
# 否则服务端比对 Sec-CH-UA-Platform 与 navigator.platform 就能发现矛盾。
_CH_PLATFORM = '"Windows"'
_CH_PLATFORM_VERSION = '"15.0.0"'  # Windows 11 的 UA-CH 平台版本

_config: dict = {}

# 浏览器路径探测顺序。容器/服务器上 DrissionPage 默认找不到 Chrome，
# 需要显式指定；这里做成"探测到就用"，避免把路径硬编码进配置文件。
#
# 说明：除常见的系统安装位置外，还支持通过环境变量
# GROK_BROWSER_PATH / PLAYWRIGHT_BROWSERS_PATH 指定，便于在
# 浏览器安装在非标准目录（如持久化数据卷）时复用。
_BROWSER_CANDIDATES = (
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/opt/google/chrome/chrome",
    "/root/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome",
)


def _candidate_from_env() -> str:
    """从环境变量推导浏览器路径。

    GROK_BROWSER_PATH 直接给出可执行文件；
    PLAYWRIGHT_BROWSERS_PATH 给出浏览器根目录，需再拼上 chromium 子路径。
    """
    direct = str(os.environ.get("GROK_BROWSER_PATH") or "").strip()
    if direct and os.path.isfile(direct) and os.access(direct, os.X_OK):
        return direct
    root = str(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if root and os.path.isdir(root):
        # 目录名带版本号，按名字排序取最新的一个。
        try:
            entries = sorted(
                name for name in os.listdir(root) if name.startswith("chromium-")
            )
        except OSError:
            entries = []
        for name in reversed(entries):
            candidate = os.path.join(root, name, "chrome-linux64", "chrome")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            candidate = os.path.join(root, name, "chrome-linux", "chrome")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return ""


def detect_browser_path() -> str:
    """返回可用的 Chromium 可执行文件路径，找不到则返回空串。

    优先使用配置项 browser_path；其次读环境变量；最后按候选列表探测。
    """
    configured = str(_config.get("browser_path") or "").strip()
    if configured and os.path.isfile(configured) and os.access(configured, os.X_OK):
        return configured
    from_env = _candidate_from_env()
    if from_env:
        return from_env
    for candidate in _BROWSER_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def configure(config_ref) -> None:
    """由 app_config 注入配置。"""
    global _config
    _config = config_ref if isinstance(config_ref, dict) else {}


def enabled() -> bool:
    return bool(_config.get("us_consistency_enabled", True))


def timezone_name() -> str:
    return str(_config.get("us_consistency_timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE


def locale_name() -> str:
    return str(_config.get("us_consistency_locale") or DEFAULT_LOCALE).strip() or DEFAULT_LOCALE


def apply_process_timezone() -> str:
    """在进程级设置 TZ，使 Chromium 子进程继承正确时区。

    Chromium 在 Linux 上读取 TZ 环境变量决定本地时区；这是最可靠、
    且不依赖 CDP 的注入方式（CDP 的 Emulation.setTimezoneOverride 只在
    已建立的 target 上生效，新开的 tab 需要重新注入）。

    返回实际生效的时区名。
    """
    if not enabled():
        return ""
    tz = timezone_name()
    os.environ["TZ"] = tz
    try:
        time.tzset()  # POSIX: 让 C 库立即感知新的 TZ
    except Exception:
        pass
    return tz


def apply_browser_options(options) -> None:
    """把一致性参数写进 DrissionPage ChromiumOptions。

    必须在 Chromium 启动前调用。同时负责指定浏览器可执行文件路径 ——
    容器/服务器环境里 DrissionPage 默认找不到 Chrome，不指定会直接报
    "Browser not found"。
    """
    if not enabled():
        return
    locale = locale_name()

    # 浏览器路径：容器内通常需要显式指定，否则 DrissionPage 找不到可执行文件。
    browser_path = detect_browser_path()
    if browser_path:
        try:
            options.set_browser_path(browser_path)
        except Exception:
            pass

    # --lang 影响 navigator.language 与 Accept-Language 的默认值。
    try:
        options.set_argument("--lang=%s" % locale)
    except Exception:
        pass

    # 关闭可能暴露自动化/精简环境的开关，并统一语言列表。
    # --no-sandbox / --disable-dev-shm-usage 是容器内运行的必需项：
    # 容器通常无特权且 /dev/shm 偏小，缺失会导致浏览器启动失败。
    for argument in (
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--accept-lang=%s" % DEFAULT_ACCEPT_LANGUAGE,
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints",
        "--disable-blink-features=AutomationControlled",
    ):
        try:
            options.set_argument(argument)
        except Exception:
            pass


def page_override_script() -> str:
    """返回注入到每个新文档的 JS：抹平 navigator 上的平台矛盾。

    注意 platform 不能只靠 CDP setUserAgentOverride —— 那个接口会顺带
    把 navigator.languages 覆盖成 ["en-US","en;q=0.9"] 这种非法形态，
    反而制造出新的异常。这里用 JS 直接定义 getter，更干净可控。
    """
    locale = locale_name()
    return """
(() => {
  const define = (obj, prop, value) => {
    try {
      Object.defineProperty(obj, prop, { get: () => value, configurable: true });
    } catch (e) {}
  };
  define(navigator, 'platform', '%(platform)s');
  define(navigator, 'hardwareConcurrency', %(cores)d);
  define(navigator, 'deviceMemory', %(memory)d);
  define(navigator, 'languages', ['%(locale)s', 'en']);
  define(navigator, 'language', '%(locale)s');
  define(navigator, 'webdriver', undefined);
  // Chrome 对象存在性：headless 下可能缺失，补齐以免被识别。
  if (!window.chrome) { window.chrome = {}; }
  if (!window.chrome.runtime) { window.chrome.runtime = {}; }
})();
""" % {
        "platform": DEFAULT_PLATFORM,
        "cores": DEFAULT_CORES,
        "memory": DEFAULT_DEVICE_MEMORY,
        "locale": locale,
    }


def apply_page_overrides(page) -> bool:
    """对已打开的 page 注入一致性脚本与 Client Hints 覆盖。

    在每次导航前调用；DrissionPage 的 run_cdp 直连 CDP，注入后对
    后续导航持续生效（Page.addScriptToEvaluateOnNewDocument 是持久的）。
    """
    if not enabled() or page is None:
        return False
    ok = False
    try:
        page.run_cdp("Page.addScriptToEvaluateOnNewDocument", source=page_override_script())
        ok = True
    except Exception:
        pass
    # Client Hints 与 UA 自洽：UA 声称 Windows/Chrome，这些头必须跟上。
    for header, value in (
        ("Sec-CH-UA-Platform", _CH_PLATFORM),
        ("Sec-CH-UA-Platform-Version", _CH_PLATFORM_VERSION),
        ("Accept-Language", DEFAULT_ACCEPT_LANGUAGE),
    ):
        try:
            page.run_cdp("Network.setExtraHTTPHeaders", headers={header: value})
        except Exception:
            pass
    return ok


def describe() -> str:
    """返回人类可读的一致性摘要，用于启动日志。"""
    if not enabled():
        return "美国环境一致性: 已关闭（原版行为）"
    return "美国环境一致性: 已启用 (时区=%s, 语言=%s, 平台=%s)" % (
        timezone_name(),
        locale_name(),
        DEFAULT_PLATFORM,
    )

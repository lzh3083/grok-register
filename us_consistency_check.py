"""出口国家 ↔ 浏览器时区/语言 一致性校验。

为什么需要
----------
"美国住宅 IP + 美国时区" 才是一致的组合。但代理池可能因为节点耗尽、
地区误配或回退策略，实际给出非美国出口。此时若仍用 America/New_York，
就制造了新的矛盾（IP=JP，时区=US），比不设时区更容易被识别。

本工具在正式跑批前做一次自检：拿一个真实节点，探测出口国家，
再和配置里的时区/语言比对，给出明确结论。
"""

from __future__ import annotations

import json
import urllib.request

# 国家码 -> (建议时区, 建议语言)。只覆盖常用地区。
COUNTRY_PROFILE = {
    "US": ("America/New_York", "en-US"),
    "CA": ("America/Toronto", "en-CA"),
    "GB": ("Europe/London", "en-GB"),
    "DE": ("Europe/Berlin", "de-DE"),
    "FR": ("Europe/Paris", "fr-FR"),
    "JP": ("Asia/Tokyo", "ja-JP"),
    "KR": ("Asia/Seoul", "ko-KR"),
    "SG": ("Asia/Singapore", "en-SG"),
    "HK": ("Asia/Hong_Kong", "zh-HK"),
    "TW": ("Asia/Taipei", "zh-TW"),
    "AU": ("Australia/Sydney", "en-AU"),
    "NL": ("Europe/Amsterdam", "nl-NL"),
    "ES": ("Europe/Madrid", "es-ES"),
    "IT": ("Europe/Rome", "it-IT"),
    "BR": ("America/Sao_Paulo", "pt-BR"),
    "IN": ("Asia/Kolkata", "en-IN"),
}


def probe_exit(proxy_url: str = "", timeout: float = 25.0) -> dict:
    """探测出口 IP 的国家与 ISP 属性。

    proxy_url 为空时走直连。返回 dict，失败时 ok=False 并带 error。

    这里用 requests 而不是 urllib：urllib 不支持 socks5h://，
    而本机 DNS 被 fake-ip 劫持（198.18.0.0/15），必须用远端解析。
    """
    result = {"ok": False, "ip": "", "country": "", "isp": "", "hosting": None, "error": ""}
    try:
        import requests
    except Exception as exc:  # pragma: no cover - 依赖缺失时明确报错
        result["error"] = "缺少 requests 依赖: %s" % exc
        return result
    proxies = None
    if proxy_url:
        url = proxy_url if "://" in proxy_url else "http://%s" % proxy_url
        proxies = {"http": url, "https": url}
    try:
        response = requests.get(
            "http://ip-api.com/json/?fields=status,country,countryCode,isp,org,as,hosting,proxy,mobile,query",
            proxies=proxies, timeout=timeout,
        )
        data = response.json()
    except Exception as exc:
        result["error"] = "探测失败: %s" % exc
        return result
    if not isinstance(data, dict) or data.get("status") != "success":
        result["error"] = "探测接口返回异常: %r" % (data,)
        return result
    result.update({
        "ok": True,
        "ip": str(data.get("query") or ""),
        "country": str(data.get("countryCode") or "").upper(),
        "isp": str(data.get("isp") or ""),
        "hosting": data.get("hosting"),
    })
    return result


def check(proxy_url: str, timezone: str, locale: str, expect_country: str = "US") -> dict:
    """校验"实际出口国家"与"配置的时区/语言"是否自洽。

    返回 {"ok", "level", "messages": [...], "exit": {...}}。
    level: ok / warn / fail
    """
    exit_info = probe_exit(proxy_url)
    messages = []
    if not exit_info["ok"]:
        return {
            "ok": False, "level": "fail", "exit": exit_info,
            "messages": ["无法探测出口：%s" % exit_info["error"]],
        }

    country = exit_info["country"]
    profile = COUNTRY_PROFILE.get(country)
    level = "ok"

    if expect_country and country != expect_country.upper():
        level = "fail"
        messages.append(
            "出口国家不符：期望 %s，实际 %s（IP %s, %s）"
            % (expect_country.upper(), country or "未知", exit_info["ip"], exit_info["isp"])
        )

    if profile:
        want_tz, want_locale = profile
        if timezone != want_tz:
            level = "fail" if level == "fail" else "warn"
            messages.append(
                "时区与出口国家不一致：出口 %s 建议时区 %s，当前配置 %s —— "
                "IP 国家与时区矛盾本身就是风控特征" % (country, want_tz, timezone)
            )
        if locale.split("-")[0] != want_locale.split("-")[0]:
            level = "fail" if level == "fail" else "warn"
            messages.append(
                "语言与出口国家不一致：出口 %s 建议语言 %s，当前配置 %s"
                % (country, want_locale, locale)
            )
    else:
        messages.append("出口国家 %s 不在内置建议表中，请自行确认时区/语言是否匹配" % country)

    if exit_info.get("hosting"):
        messages.append(
            "注意：该出口被标记为机房 IP（hosting=true, %s）—— "
            "住宅代理不应出现此标记" % exit_info["isp"]
        )
        if level == "ok":
            level = "warn"

    if level == "ok" and not messages:
        messages.append(
            "一致：出口 %s（%s），时区 %s，语言 %s"
            % (country, exit_info["isp"], timezone, locale)
        )
    return {"ok": level == "ok", "level": level, "exit": exit_info, "messages": messages}


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="us_consistency_check",
        description="校验出口国家与浏览器时区/语言是否一致",
    )
    parser.add_argument("--proxy", required=True, help="待校验的代理地址，如 http://1.2.3.4:8080")
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--expect", default="US", help="期望的出口国家码")
    args = parser.parse_args(argv)

    report = check(args.proxy, args.timezone, args.locale, args.expect)
    icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}[report["level"]]
    print("%s 校验结果: %s" % (icon, report["level"].upper()))
    for message in report["messages"]:
        print("   - %s" % message)
    return 0 if report["level"] != "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())

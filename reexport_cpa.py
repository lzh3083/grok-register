#!/usr/bin/env python
"""为已注册成功但 CPA 导出失败的账号补导出 OIDC 凭据。

背景：住宅代理节点会动态掉线。若 CPA 导出阶段节点失效，注册成果仍然
保留（账号已入库），只是凭据没导出。此脚本复用已保存的 SSO Cookie
补跑导出，无需重新注册。

用法：
    # 从账号文件补导出全部缺失的凭据
    python reexport_cpa.py --accounts accounts_20260925_135746.txt

    # 只补指定邮箱
    python reexport_cpa.py --email 8n8bd5mjzlw@jgxjs.com

    # 先看会做什么，不实际执行
    python reexport_cpa.py --accounts accounts_xxx.txt --dry-run

账号文件格式（项目既有格式）：
    email----password----sso_cookie
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import app_config  # noqa: E402
import cpa_export  # noqa: E402


def load_accounts(path):
    """读取账号文件，返回 [{'email','password','sso'}, ...]。"""
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.count("----") < 2:
                continue
            parts = line.split("----")
            rows.append({
                "email": parts[0].strip(),
                "password": parts[1].strip() if len(parts) > 1 else "",
                "sso": parts[2].strip() if len(parts) > 2 else "",
            })
    return rows


def existing_emails(auth_dir):
    """已成功导出的邮箱集合。"""
    found = set()
    for path in glob.glob(str(Path(auth_dir) / "xai-*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                found.add(str(json.load(handle).get("email") or "").strip().lower())
        except Exception:
            continue
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description="补导出缺失的 CPA OIDC 凭据")
    parser.add_argument("--accounts", default="", help="账号文件路径（可多次指定）")
    parser.add_argument("--email", default="", help="只处理指定邮箱")
    parser.add_argument("--dry-run", action="store_true", help="只列出待处理项，不执行")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    args = parser.parse_args(argv)

    with open(args.config, encoding="utf-8") as handle:
        config = app_config.validate_config(json.load(handle))
    auth_dir = Path(str(config.get("cpa_auth_dir") or "./cpa_auths"))

    # CPA 导出会另起一个独立 Chromium（force_standalone），必须先把
    # 浏览器路径与一致性参数注入运行时，否则 DrissionPage 找不到可执行
    # 文件，报 "The browser executable file path cannot be found"。
    import browser_runtime
    import us_consistency
    browser_runtime.configure_runtime(config)
    us_consistency.configure(config)
    us_consistency.apply_process_timezone()

    files = []
    if args.accounts:
        files = [args.accounts]
    else:
        files = sorted(glob.glob("accounts_*.txt"))
    if not files:
        print("  未找到账号文件（accounts_*.txt）")
        return 2

    done = existing_emails(auth_dir)
    todo = []
    seen = set()
    for path in files:
        for row in load_accounts(path):
            email = row["email"]
            if not email or email.lower() in seen:
                continue
            seen.add(email.lower())
            if args.email and email.lower() != args.email.lower():
                continue
            if email.lower() in done:
                continue
            if not row["sso"]:
                print("  ⚠️  跳过 %s：账号文件里没有 SSO Cookie" % email)
                continue
            todo.append(row)

    print("  已导出: %d 个" % len(done))
    print("  待补导出: %d 个" % len(todo))
    for row in todo:
        print("     - %s" % row["email"])
    if not todo:
        print("  ✅ 没有需要补导出的账号")
        return 0
    if args.dry_run:
        print("\n  （--dry-run，未实际执行）")
        return 0

    print()
    ok = 0
    for row in todo:
        print("  → 正在补导出 %s ..." % row["email"])
        result = cpa_export.export_cpa_xai_for_account(
            email=row["email"], password=row["password"], sso=row["sso"],
            config=config, log_callback=lambda message: print("     " + str(message)),
        )
        if result.get("ok"):
            ok += 1
            print("     ✅ 成功: %s" % result.get("path"))
        else:
            print("     ❌ 失败: %s" % (result.get("error") or "unknown"))
    print()
    print("  完成: 成功 %d / 共 %d" % (ok, len(todo)))
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    sys.exit(main())

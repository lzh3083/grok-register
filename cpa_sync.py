"""把导出的 CPA 凭据同步到远程 CPA 实例。

为什么需要
----------
项目的 `cpa_copy_to_hotload` 只支持复制到**本机目录**。当 CPA 部署在
另一台服务器（Docker 卷映射）时无法使用，需要远程同步。

实测 CPA 会**自动扫描 auth 目录**：新增凭据文件后立即生效，无需重启
或调用管理接口（该实例也没有上传端点）。所以同步只需把文件放到位。

用法：
    # 测试连通性与目标目录
    python cpa_sync.py --check

    # 同步全部本地凭据（跳过已存在的）
    python cpa_sync.py --sync

    # 强制覆盖远程同名文件
    python cpa_sync.py --sync --force

配置（config.json）：
    cpa_sync_enabled  : 是否在每次导出后自动同步
    cpa_sync_target   : SSH 目标，如 user@host
    cpa_sync_auth_dir : 远程 auth 目录（宿主机路径，非容器内路径）
    cpa_sync_use_sudo : 目标目录需 root 权限时置 true
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class SyncError(RuntimeError):
    pass


def _run(cmd, timeout=60, input_text=None):
    """执行命令，返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, input=input_text,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "命令超时（%ss）" % timeout
    except FileNotFoundError:
        return 127, "", "命令不存在: %s" % cmd[0]


def check_target(target, auth_dir, use_sudo=True, log=print):
    """检查 SSH 连通性与远程目录是否可写。"""
    if not target:
        raise SyncError("未配置 cpa_sync_target")
    if not auth_dir:
        raise SyncError("未配置 cpa_sync_auth_dir")

    code, out, err = _run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                           target, "echo SYNC-OK"], timeout=30)
    if code != 0 or "SYNC-OK" not in out:
        raise SyncError("SSH 连接失败: %s" % (err.strip() or out.strip() or "未知错误"))
    log("  ✅ SSH 可达: %s" % target)

    # 不加尾随空格：格式串里已有空格，否则会拼出双空格。
    sudo = "sudo" if use_sudo else ""
    code, out, err = _run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", target,
         ("%s test -d '%s' && echo DIR-OK || echo DIR-MISSING" % (sudo, auth_dir)).strip()],
        timeout=30,
    )
    if "DIR-OK" not in out:
        raise SyncError("远程目录不存在或不可读: %s" % auth_dir)
    log("  ✅ 目标目录存在: %s" % auth_dir)

    code, out, err = _run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", target,
         ("%s test -w '%s' && echo WRITABLE || echo READONLY" % (sudo, auth_dir)).strip()],
        timeout=30,
    )
    if "WRITABLE" not in out:
        raise SyncError(
            "远程目录不可写: %s（若需 root 权限，请设置 cpa_sync_use_sudo=true）" % auth_dir
        )
    log("  ✅ 目录可写")
    return True


def remote_emails(target, auth_dir, use_sudo=True, timeout=60):
    """列出远程已有的凭据文件名。"""
    # 不加尾随空格：格式串里已有空格，否则会拼出双空格。
    sudo = "sudo" if use_sudo else ""
    code, out, err = _run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", target,
         ("%s ls -1 '%s' 2>/dev/null | grep '^xai-' || true" % (sudo, auth_dir)).strip()],
        timeout=timeout,
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def sync_files(target, auth_dir, files, use_sudo=True, force=False, log=print):
    """把凭据文件同步到远程目录。

    先传到 /tmp，再用 sudo mv 就位 —— 目标目录通常只有 root 可写，
    直接 scp 到目标目录会因权限失败。
    """
    if not files:
        log("  ✅ 没有需要同步的凭据")
        return {"synced": [], "skipped": [], "failed": []}

    existing = remote_emails(target, auth_dir, use_sudo=use_sudo)
    pending = []
    skipped = []
    for path in files:
        name = os.path.basename(path)
        if name in existing and not force:
            skipped.append(name)
            continue
        pending.append((path, name))

    for name in skipped:
        log("  ⏭️  已存在，跳过: %s" % name)
    if not pending:
        log("  ✅ 全部已同步")
        return {"synced": [], "skipped": skipped, "failed": []}

    # 批量上传到 /tmp，减少 SSH 往返
    tmpdir = "/tmp/cpa-sync-%d" % os.getpid()
    code, out, err = _run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", target,
         "mkdir -p '%s' && chmod 700 '%s'" % (tmpdir, tmpdir)], timeout=30,
    )
    if code != 0:
        raise SyncError("创建临时目录失败: %s" % err.strip())

    code, out, err = _run(
        ["scp", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", "-q"]
        + [path for path, _ in pending] + ["%s:%s/" % (target, tmpdir)],
        timeout=180,
    )
    if code != 0:
        raise SyncError("上传失败: %s" % (err.strip() or out.strip()))

    # 不加尾随空格：格式串里已有空格，否则会拼出双空格。
    sudo = "sudo" if use_sudo else ""
    # tmpdir 是本次新建的，里面只有刚上传的凭据，用通配复制即可。
    # 注意：不能用 '{a,b}' 形式 —— 花括号在引号内不做展开。
    code, out, err = _run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", target,
         "%s cp -f '%s'/*.json '%s/' && "
         "%s chmod 600 '%s'/xai-*.json && "
         "(%s chown root:root '%s'/xai-*.json 2>/dev/null || true); "
         "rm -rf '%s'; echo DONE"
         % (sudo, tmpdir, auth_dir, sudo, auth_dir, sudo, auth_dir, tmpdir)],
        timeout=120,
    )
    if "DONE" not in out:
        raise SyncError("安装失败: %s" % (err.strip() or out.strip()))

    synced = [name for _, name in pending]
    for name in synced:
        log("  ✅ 已同步: %s" % name)
    return {"synced": synced, "skipped": skipped, "failed": []}


def sync_from_config(config, log=print, force=False):
    """按配置同步本地 auth_dir 下的全部凭据。"""
    if not config.get("cpa_sync_enabled"):
        log("  ⏭️  未启用 CPA 自动同步（cpa_sync_enabled=false）")
        return {"skipped_all": True}
    target = str(config.get("cpa_sync_target") or "").strip()
    auth_dir = str(config.get("cpa_sync_auth_dir") or "").strip()
    use_sudo = bool(config.get("cpa_sync_use_sudo", True))
    local_dir = str(config.get("cpa_auth_dir") or "./cpa_auths")
    files = sorted(glob.glob(os.path.join(local_dir, "xai-*.json")))
    log("  [cpa-sync] 本地凭据 %d 个 → %s:%s" % (len(files), target, auth_dir))
    check_target(target, auth_dir, use_sudo=use_sudo, log=log)
    return sync_files(target, auth_dir, files, use_sudo=use_sudo, force=force, log=log)


def main(argv=None):
    parser = argparse.ArgumentParser(description="同步 CPA 凭据到远程实例")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--check", action="store_true", help="只检查连通性与目录")
    parser.add_argument("--sync", action="store_true", help="执行同步")
    parser.add_argument("--force", action="store_true", help="覆盖远程同名文件")
    parser.add_argument("--target", default="", help="临时指定 SSH 目标")
    parser.add_argument("--auth-dir", default="", help="临时指定远程目录")
    args = parser.parse_args(argv)

    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)

    target = args.target or str(config.get("cpa_sync_target") or "").strip()
    auth_dir = args.auth_dir or str(config.get("cpa_sync_auth_dir") or "").strip()
    use_sudo = bool(config.get("cpa_sync_use_sudo", True))

    if not target or not auth_dir:
        print("  ❌ 请先配置 cpa_sync_target 与 cpa_sync_auth_dir")
        return 2

    try:
        check_target(target, auth_dir, use_sudo=use_sudo)
    except SyncError as exc:
        print("  ❌ %s" % exc)
        return 1

    if args.check and not args.sync:
        print("\n  ✅ 检查通过")
        return 0

    local_dir = str(config.get("cpa_auth_dir") or "./cpa_auths")
    files = sorted(glob.glob(os.path.join(local_dir, "xai-*.json")))
    print("\n  本地凭据 %d 个" % len(files))
    try:
        result = sync_files(target, auth_dir, files, use_sudo=use_sudo, force=args.force)
    except SyncError as exc:
        print("  ❌ %s" % exc)
        return 1
    print("\n  完成: 同步 %d | 跳过 %d" % (len(result["synced"]), len(result["skipped"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

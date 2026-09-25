"""cpa_sync 远程同步模块的单元测试。

这些测试不触碰真实 SSH：subprocess.run 被替换为假实现，
只验证命令拼装、跳过逻辑与错误处理。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cpa_sync  # noqa: E402


class CheckTargetTests(unittest.TestCase):
    def test_missing_target_raises(self):
        with self.assertRaises(cpa_sync.SyncError):
            cpa_sync.check_target("", "/some/dir", log=lambda _m: None)

    def test_missing_auth_dir_raises(self):
        with self.assertRaises(cpa_sync.SyncError):
            cpa_sync.check_target("user@host", "", log=lambda _m: None)

    def test_ssh_failure_raises(self):
        with mock.patch.object(cpa_sync, "_run", return_value=(255, "", "denied")):
            with self.assertRaises(cpa_sync.SyncError) as ctx:
                cpa_sync.check_target("user@host", "/dir", log=lambda _m: None)
            self.assertIn("SSH", str(ctx.exception))

    def test_missing_dir_raises(self):
        def fake_run(cmd, timeout=60, input_text=None):
            if "SYNC-OK" in cmd[-1]:
                return 0, "SYNC-OK\n", ""
            return 0, "DIR-MISSING\n", ""

        with mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
            with self.assertRaises(cpa_sync.SyncError) as ctx:
                cpa_sync.check_target("user@host", "/dir", log=lambda _m: None)
            self.assertIn("不存在", str(ctx.exception))

    def test_readonly_dir_raises(self):
        def fake_run(cmd, timeout=60, input_text=None):
            if "SYNC-OK" in cmd[-1]:
                return 0, "SYNC-OK\n", ""
            if "test -d" in cmd[-1]:
                return 0, "DIR-OK\n", ""
            return 0, "READONLY\n", ""

        with mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
            with self.assertRaises(cpa_sync.SyncError) as ctx:
                cpa_sync.check_target("user@host", "/dir", log=lambda _m: None)
            self.assertIn("不可写", str(ctx.exception))

    def test_success(self):
        def fake_run(cmd, timeout=60, input_text=None):
            if "SYNC-OK" in cmd[-1]:
                return 0, "SYNC-OK\n", ""
            if "test -d" in cmd[-1]:
                return 0, "DIR-OK\n", ""
            return 0, "WRITABLE\n", ""

        with mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
            self.assertTrue(cpa_sync.check_target("user@host", "/dir", log=lambda _m: None))

    def test_sudo_prefix_included_when_requested(self):
        seen = []

        def fake_run(cmd, timeout=60, input_text=None):
            seen.append(cmd[-1])
            if "SYNC-OK" in cmd[-1]:
                return 0, "SYNC-OK\n", ""
            if "test -d" in cmd[-1]:
                return 0, "DIR-OK\n", ""
            return 0, "WRITABLE\n", ""

        with mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
            cpa_sync.check_target("user@host", "/dir", use_sudo=True, log=lambda _m: None)
        self.assertTrue(any("sudo test -d" in c for c in seen))

    def test_no_sudo_when_disabled(self):
        seen = []

        def fake_run(cmd, timeout=60, input_text=None):
            seen.append(cmd[-1])
            if "SYNC-OK" in cmd[-1]:
                return 0, "SYNC-OK\n", ""
            if "test -d" in cmd[-1]:
                return 0, "DIR-OK\n", ""
            return 0, "WRITABLE\n", ""

        with mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
            cpa_sync.check_target("user@host", "/dir", use_sudo=False, log=lambda _m: None)
        self.assertFalse(any("sudo" in c for c in seen))


class SyncFilesTests(unittest.TestCase):
    def _make_files(self, tmpdir, names):
        paths = []
        for name in names:
            path = Path(tmpdir) / name
            path.write_text('{"email":"%s"}' % name, encoding="utf-8")
            paths.append(str(path))
        return paths

    def test_empty_file_list(self):
        result = cpa_sync.sync_files("user@host", "/dir", [], log=lambda _m: None)
        self.assertEqual(result["synced"], [])
        self.assertEqual(result["skipped"], [])

    def test_skips_existing_unless_forced(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            files = self._make_files(tmpdir, ["xai-a@x.com.json"])
            with mock.patch.object(cpa_sync, "remote_emails", return_value={"xai-a@x.com.json"}):
                result = cpa_sync.sync_files("user@host", "/dir", files, log=lambda _m: None)
            self.assertEqual(result["synced"], [])
            self.assertEqual(result["skipped"], ["xai-a@x.com.json"])

    def test_force_overrides_skip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            files = self._make_files(tmpdir, ["xai-a@x.com.json"])
            calls = []

            def fake_run(cmd, timeout=60, input_text=None):
                calls.append(cmd)
                if cmd[0] == "ssh" and "mkdir" in cmd[-1]:
                    return 0, "", ""
                if cmd[0] == "scp":
                    return 0, "", ""
                return 0, "DONE\n", ""

            with mock.patch.object(cpa_sync, "remote_emails", return_value={"xai-a@x.com.json"}), \
                    mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
                result = cpa_sync.sync_files("user@host", "/dir", files, force=True, log=lambda _m: None)
            self.assertEqual(result["synced"], ["xai-a@x.com.json"])
            self.assertEqual(result["skipped"], [])

    def test_upload_failure_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            files = self._make_files(tmpdir, ["xai-a@x.com.json"])

            def fake_run(cmd, timeout=60, input_text=None):
                if cmd[0] == "ssh":
                    return 0, "", ""
                return 1, "", "scp: connection lost"

            with mock.patch.object(cpa_sync, "remote_emails", return_value=set()), \
                    mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
                with self.assertRaises(cpa_sync.SyncError) as ctx:
                    cpa_sync.sync_files("user@host", "/dir", files, log=lambda _m: None)
                self.assertIn("上传失败", str(ctx.exception))

    def test_install_failure_raises(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            files = self._make_files(tmpdir, ["xai-a@x.com.json"])

            def fake_run(cmd, timeout=60, input_text=None):
                if cmd[0] == "ssh" and "mkdir" in cmd[-1]:
                    return 0, "", ""
                if cmd[0] == "scp":
                    return 0, "", ""
                return 1, "", "permission denied"

            with mock.patch.object(cpa_sync, "remote_emails", return_value=set()), \
                    mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
                with self.assertRaises(cpa_sync.SyncError) as ctx:
                    cpa_sync.sync_files("user@host", "/dir", files, log=lambda _m: None)
                self.assertIn("安装失败", str(ctx.exception))

    def test_successful_sync(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            files = self._make_files(tmpdir, ["xai-a@x.com.json", "xai-b@x.com.json"])
            scp_calls = []

            def fake_run(cmd, timeout=60, input_text=None):
                if cmd[0] == "scp":
                    scp_calls.append(cmd)
                    return 0, "", ""
                if "mkdir" in cmd[-1]:
                    return 0, "", ""
                return 0, "DONE\n", ""

            with mock.patch.object(cpa_sync, "remote_emails", return_value=set()), \
                    mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
                result = cpa_sync.sync_files("user@host", "/dir", files, log=lambda _m: None)
            self.assertEqual(sorted(result["synced"]), ["xai-a@x.com.json", "xai-b@x.com.json"])
            # 两个文件应合并为一次 scp，减少 SSH 往返
            self.assertEqual(len(scp_calls), 1)
            self.assertIn("user@host:/tmp/cpa-sync-%d/" % os.getpid(), scp_calls[0])

    def test_cleanup_tmpdir_in_command(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            files = self._make_files(tmpdir, ["xai-a@x.com.json"])
            install_cmds = []

            def fake_run(cmd, timeout=60, input_text=None):
                if cmd[0] == "ssh" and "mkdir" in cmd[-1]:
                    return 0, "", ""
                if cmd[0] == "scp":
                    return 0, "", ""
                install_cmds.append(cmd[-1])
                return 0, "DONE\n", ""

            with mock.patch.object(cpa_sync, "remote_emails", return_value=set()), \
                    mock.patch.object(cpa_sync, "_run", side_effect=fake_run):
                cpa_sync.sync_files("user@host", "/dir", files, log=lambda _m: None)
            self.assertTrue(any("rm -rf" in c for c in install_cmds))
            self.assertTrue(any("chmod 600" in c for c in install_cmds))


class RemoteEmailsTests(unittest.TestCase):
    def test_parses_listing(self):
        with mock.patch.object(cpa_sync, "_run", return_value=(0, "xai-a@x.com.json\nxai-b@x.com.json\n", "")):
            result = cpa_sync.remote_emails("user@host", "/dir")
        self.assertEqual(result, {"xai-a@x.com.json", "xai-b@x.com.json"})

    def test_empty_listing(self):
        with mock.patch.object(cpa_sync, "_run", return_value=(0, "", "")):
            self.assertEqual(cpa_sync.remote_emails("user@host", "/dir"), set())


class SyncFromConfigTests(unittest.TestCase):
    def test_disabled_returns_early(self):
        result = cpa_sync.sync_from_config({"cpa_sync_enabled": False}, log=lambda _m: None)
        self.assertTrue(result.get("skipped_all"))

    def test_missing_target_raises(self):
        with self.assertRaises(cpa_sync.SyncError):
            cpa_sync.sync_from_config(
                {"cpa_sync_enabled": True, "cpa_sync_target": "", "cpa_sync_auth_dir": "/d"},
                log=lambda _m: None,
            )


if __name__ == "__main__":
    unittest.main()

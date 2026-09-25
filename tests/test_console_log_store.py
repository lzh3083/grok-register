"""console_log_store 的单元测试。

核心诉求：面板日志在服务重启后仍能看到。这里覆盖写入、跨天拼接、
截断、保留期清理，以及「磁盘故障不得影响面板本身」这条降级约定。
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import console_log_store  # noqa: E402


class ConsoleLogStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "logs"
        self.store = console_log_store.ConsoleLogStore(self.dir)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_write_then_read(self):
        self.store.write("first")
        self.store.write("second")
        self.assertEqual(self.store.read_recent(), ["first", "second"])

    def test_survives_reopen(self):
        """换一个实例读取同一目录 —— 模拟服务重启。"""
        self.store.write("before restart")
        self.store.close()
        reopened = console_log_store.ConsoleLogStore(self.dir)
        self.assertEqual(reopened.read_recent(), ["before restart"])
        reopened.close()

    def test_creates_directory(self):
        self.assertFalse(self.dir.exists())
        self.store.write("x")
        self.assertTrue(self.dir.is_dir())

    def test_read_recent_limits_from_tail(self):
        for i in range(50):
            self.store.write("line-%d" % i)
        lines = self.store.read_recent(limit=10)
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[-1], "line-49")
        self.assertEqual(lines[0], "line-40")

    def test_read_recent_limit_zero_means_all(self):
        for i in range(30):
            self.store.write("line-%d" % i)
        self.assertEqual(len(self.store.read_recent(limit=0)), 30)

    def test_long_line_is_truncated(self):
        self.store.write("x" * 5000)
        line = self.store.read_recent()[0]
        self.assertLessEqual(len(line), console_log_store.MAX_LINE_CHARS + 20)
        self.assertIn("已截断", line)

    def test_newlines_are_flattened(self):
        """多行内容必须压成一行，否则读取时会被拆成多条。"""
        self.store.write("a\nb\r\nc")
        self.assertEqual(len(self.store.read_recent()), 1)

    def test_empty_and_none_are_safe(self):
        self.store.write("")
        self.store.write(None)
        self.assertEqual(len(self.store.read_recent()), 2)

    def test_cross_day_files_are_concatenated_in_order(self):
        """跨天时按日期升序拼接，保证时间顺序。"""
        (Path(self.tmp.name) / "logs").mkdir(parents=True, exist_ok=True)
        self.store.write("today")
        self.store.close()
        # 手工造一个更早的日志文件
        older = self.store.path_for("2020-01-01")
        older.write_text("old-1\nold-2\n", encoding="utf-8")
        reopened = console_log_store.ConsoleLogStore(self.dir)
        lines = reopened.read_recent()
        self.assertEqual(lines[:2], ["old-1", "old-2"])
        self.assertEqual(lines[-1], "today")
        reopened.close()

    def test_prune_removes_old_files_only(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        old = self.store.path_for("2020-01-01")
        old.write_text("old\n", encoding="utf-8")
        recent = self.store.path_for(datetime.date.today().strftime("%Y-%m-%d"))
        recent.write_text("new\n", encoding="utf-8")
        removed = self.store.prune()
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_prune_ignores_unrelated_files(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        other = self.dir / "notes.txt"
        other.write_text("keep me", encoding="utf-8")
        self.store.prune()
        self.assertTrue(other.exists())

    def test_unwritable_directory_degrades_gracefully(self):
        """落盘失败时降级为可用对象，不抛异常。"""
        store = console_log_store.ConsoleLogStore("/proc/definitely/not/writable")
        store.write("should not raise")
        self.assertEqual(store.read_recent(), [])
        store.close()

    def test_retention_is_at_least_one_day(self):
        store = console_log_store.ConsoleLogStore(self.dir, retention_days=0)
        self.assertGreaterEqual(store.retention_days, 1)

    def test_file_permissions_are_restrictive(self):
        self.store.write("secret-ish")
        self.store.flush()
        path = self.store.path_for(datetime.date.today().strftime("%Y-%m-%d"))
        mode = os.stat(str(path)).st_mode & 0o777
        self.assertEqual(mode, 0o600, oct(mode))

    def test_read_recent_on_missing_dir(self):
        empty = console_log_store.ConsoleLogStore(Path(self.tmp.name) / "nope")
        self.assertEqual(empty.read_recent(), [])
        empty.close()


if __name__ == "__main__":
    unittest.main()

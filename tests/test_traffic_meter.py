"""traffic_meter 批次代理流量计量的单元测试。

计量本身只是字节累加，但有两个容易出错的地方值得钉住：
1. 累加必须是线程安全的（中继是多线程并发）。
2. 落盘必须是原子写，否则面板会读到半截 JSON。
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import traffic_meter  # noqa: E402


class TrafficMeterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.traffic = Path(self.tmp.name) / "traffic.json"
        self.history = Path(self.tmp.name) / "history.json"
        self.env = mock.patch.dict(os.environ, {
            traffic_meter.TRAFFIC_FILE_ENV: str(self.traffic),
            traffic_meter.HISTORY_FILE_ENV: str(self.history),
        })
        self.env.start()
        # 每个用例从干净状态开始
        with traffic_meter._LOCK:
            traffic_meter._STATE.update({
                "running": False, "started_at": None, "finished_at": None,
                "bytes_up": 0, "bytes_down": 0, "connections": 0, "accounts": 0,
                "archived": False,
            })

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_record_accumulates_by_direction(self):
        traffic_meter.begin_batch()
        traffic_meter.record("up", 100)
        traffic_meter.record("up", 50)
        traffic_meter.record("down", 300)
        snap = traffic_meter.snapshot()
        self.assertEqual(snap["bytes_up"], 150)
        self.assertEqual(snap["bytes_down"], 300)
        self.assertEqual(snap["bytes_total"], 450)

    def test_record_ignores_invalid_input(self):
        traffic_meter.begin_batch()
        traffic_meter.record("up", 0)
        traffic_meter.record("up", -5)
        traffic_meter.record("up", None)
        traffic_meter.record("up", "not-a-number")
        self.assertEqual(traffic_meter.snapshot()["bytes_up"], 0)

    def test_unknown_direction_counts_as_down(self):
        traffic_meter.begin_batch()
        traffic_meter.record("sideways", 42)
        self.assertEqual(traffic_meter.snapshot()["bytes_down"], 42)

    def test_begin_batch_is_idempotent_while_running(self):
        traffic_meter.begin_batch()
        traffic_meter.record("up", 999)
        traffic_meter.begin_batch()  # 不应清零
        self.assertEqual(traffic_meter.snapshot()["bytes_up"], 999)

    def test_finish_batch_flushes_and_archives(self):
        traffic_meter.begin_batch()
        traffic_meter.record("up", 1000)
        traffic_meter.record("down", 2000)
        traffic_meter.count_account(2)
        data = traffic_meter.finish_batch()
        self.assertFalse(data["running"])
        self.assertEqual(data["bytes_total"], 3000)
        self.assertTrue(self.traffic.is_file())
        on_disk = json.loads(self.traffic.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["bytes_total"], 3000)
        history = traffic_meter.read_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["bytes_total"], 3000)
        self.assertEqual(history[0]["accounts"], 2)

    def test_finish_batch_without_begin_is_safe(self):
        data = traffic_meter.finish_batch()
        self.assertEqual(data["bytes_total"], 0)

    def test_history_is_newest_first_and_capped(self):
        for i in range(5):
            traffic_meter.begin_batch()
            traffic_meter.record("up", (i + 1) * 100)
            traffic_meter.finish_batch()
        history = traffic_meter.read_history()
        self.assertEqual(len(history), 5)
        self.assertEqual(history[0]["bytes_up"], 500)  # 最新在前
        self.assertEqual(history[-1]["bytes_up"], 100)

    def test_read_metrics_falls_back_to_memory(self):
        traffic_meter.begin_batch()
        traffic_meter.record("up", 777)
        # 文件尚不存在时读内存
        metrics = traffic_meter.read_metrics()
        self.assertEqual(metrics["bytes_up"], 777)

    def test_concurrent_records_are_thread_safe(self):
        traffic_meter.begin_batch()
        per_thread = 1000
        threads = [
            threading.Thread(target=lambda: [traffic_meter.record("up", 10) for _ in range(per_thread)])
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(traffic_meter.snapshot()["bytes_up"], 8 * per_thread * 10)

    def test_format_bytes(self):
        self.assertEqual(traffic_meter.format_bytes(0), "0 B")
        self.assertEqual(traffic_meter.format_bytes(512), "512 B")
        self.assertEqual(traffic_meter.format_bytes(1536), "1.50 KB")
        self.assertEqual(traffic_meter.format_bytes(5 * 1024 ** 3), "5.00 GB")
        self.assertEqual(traffic_meter.format_bytes("bad"), "—")

    def test_write_is_atomic(self):
        """落盘后不应残留 .tmp 文件。"""
        traffic_meter.begin_batch()
        traffic_meter.record("up", 1)
        traffic_meter.flush()
        leftovers = list(self.traffic.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_timestamps_ignore_process_timezone_switch(self):
        """注册流程会 tzset() 切时区，批次时间戳不能跟着跳。

        实测踩过：begin_batch 用服务器时区、finish_batch 用美国时区，
        归档出现「结束时间比开始时间早 7 小时」，窗口统计全错。
        """
        original_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "UTC"
            time.tzset()
            # 让 _fixed_offset 在当前 TZ 下锁定
            traffic_meter._TZ_OFFSET = None
            traffic_meter.begin_batch()
            started = traffic_meter.snapshot()["started_at"]

            # 模拟 us_consistency 把进程切到美国中部时区
            os.environ["TZ"] = "America/Chicago"
            time.tzset()
            data = traffic_meter.finish_batch()

            started_dt = traffic_meter._parse_stamp(started)
            finished_dt = traffic_meter._parse_stamp(data["finished_at"])
            self.assertIsNotNone(started_dt)
            self.assertIsNotNone(finished_dt)
            # 结束时间不得早于开始时间（时区漂移会把它推早 5~7 小时）
            self.assertGreaterEqual(finished_dt, started_dt)
            self.assertLess((finished_dt - started_dt).total_seconds(), 60)
        finally:
            traffic_meter._TZ_OFFSET = None
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

    def test_window_uses_fixed_offset_not_current_tz(self):
        """window() 的「现在」也必须走固定偏移，否则切换 TZ 后窗口会漂。"""
        original_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "UTC"
            time.tzset()
            traffic_meter._TZ_OFFSET = None
            traffic_meter.begin_batch()
            traffic_meter.record("down", 4096)
            traffic_meter.finish_batch()

            os.environ["TZ"] = "America/Chicago"
            time.tzset()
            recent = traffic_meter.window(24)
            self.assertEqual(recent["bytes_down"], 4096)
            self.assertEqual(recent["batches"], 1)
        finally:
            traffic_meter._TZ_OFFSET = None
            if original_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_tz
            time.tzset()

    def test_count_account_is_archived(self):
        """账号数要进归档记录（面板按账号摊算单号成本）。"""
        traffic_meter.begin_batch()
        traffic_meter.record("down", 100)
        traffic_meter.count_account(3)
        data = traffic_meter.finish_batch()
        self.assertEqual(data["accounts"], 3)
        self.assertEqual(traffic_meter.read_history()[0]["accounts"], 3)

    def test_totals_do_not_double_count_finished_batch(self):
        """批次跑完后，总量不能把同一批算两遍（历史 + 残留内存态）。

        实测踩过：finish_batch 只把 running 置 False，字节仍留在内存，
        totals()/window() 又把它们加了一次，面板上的总量刚好是双倍。
        """
        traffic_meter.begin_batch()
        traffic_meter.record("down", 5000)
        traffic_meter.finish_batch()

        # 内存里仍能看到刚跑完的这批（面板「本批」要显示）
        self.assertEqual(traffic_meter.snapshot()["bytes_down"], 5000)
        # 但总量只能算一次
        self.assertEqual(traffic_meter.totals()["bytes_down"], 5000)
        self.assertEqual(traffic_meter.totals()["batches"], 1)
        self.assertEqual(traffic_meter.window(24)["bytes_down"], 5000)

    def test_totals_add_live_batch_while_running(self):
        """仍在跑的批次没进历史，必须由内存态补上。"""
        traffic_meter.begin_batch()
        traffic_meter.record("up", 300)
        self.assertEqual(traffic_meter.totals()["bytes_up"], 300)
        self.assertEqual(traffic_meter.totals()["batches"], 1)


if __name__ == "__main__":
    unittest.main()

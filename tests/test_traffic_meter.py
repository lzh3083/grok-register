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


if __name__ == "__main__":
    unittest.main()

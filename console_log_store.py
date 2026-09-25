"""控制台日志的持久化。

为什么需要
----------
面板日志原先只存在内存 deque 里：刷新页面能靠 /api/logs 补回来，但
**服务一重启就全丢**，排查昨天那批为什么失败时已经看不到现场。

做法
----
内存 deque 仍然保留（保证 /api/logs 的增量拉取足够快），同时在写入时
追加到磁盘文件。重启后从文件回填内存，面板无需改动即可看到历史。

文件按天切分（logs/console-YYYY-MM-DD.log），并限制保留天数，避免长期
运行把磁盘写满。写入走行缓冲 + 定期 fsync，崩溃时最多丢最后几行。
"""
from __future__ import annotations

import datetime
import os
import re
import threading
from pathlib import Path

DEFAULT_RETENTION_DAYS = 14
MAX_LINE_BYTES = 4096
# 单行超过此长度时截断，避免上游把整个 HTML 打进一行撑爆日志文件
MAX_LINE_CHARS = 2000


class ConsoleLogStore:
    """把控制台日志同时写入内存与磁盘。"""

    def __init__(self, directory, retention_days=DEFAULT_RETENTION_DAYS, log=lambda _m: None):
        self.directory = Path(directory)
        self.retention_days = max(1, int(retention_days))
        self._log = log
        self._lock = threading.RLock()
        self._handle = None
        self._current_date = ""
        self._enabled = True

    # ---- 路径 ----
    def path_for(self, date_text):
        return self.directory / ("console-%s.log" % date_text)

    def _today(self):
        return datetime.date.today().strftime("%Y-%m-%d")

    # ---- 打开 / 轮转 ----
    def _ensure_handle(self):
        today = self._today()
        if self._handle is not None and self._current_date == today:
            return self._handle
        self._close_handle()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(str(self.directory), 0o700)
            except Exception:
                pass
            path = self.path_for(today)
            handle = open(str(path), "a", encoding="utf-8")
            try:
                os.chmod(str(path), 0o600)
            except Exception:
                pass
            self._handle = handle
            self._current_date = today
        except Exception as exc:
            # 落盘失败不应影响面板本身，降级为仅内存
            self._enabled = False
            self._log("日志落盘不可用（降级为仅内存）: %s" % exc)
            return None
        return self._handle

    def _close_handle(self):
        if self._handle is not None:
            try:
                self._handle.flush()
                self._handle.close()
            except Exception:
                pass
            self._handle = None

    # ---- 写入 ----
    def write(self, line):
        """追加一行。任何异常都不得向上抛。"""
        if not self._enabled:
            return
        text = str(line or "")
        if len(text) > MAX_LINE_CHARS:
            text = text[:MAX_LINE_CHARS] + " …(已截断)"
        with self._lock:
            handle = self._ensure_handle()
            if handle is None:
                return
            try:
                # 换行必须压平：日志是一行一条，嵌入的 \n 会把单条记录
                # 拆成多条，读取时无法还原（也破坏按行解析的预期）。
                flat = " ".join(text.replace("\r", "\n").split("\n"))
                handle.write(flat.rstrip() + "\n")
                handle.flush()
            except Exception:
                self._close_handle()

    def flush(self):
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.flush()
                    os.fsync(self._handle.fileno())
                except Exception:
                    pass

    # ---- 读取 ----
    def read_recent(self, limit=2000):
        """按时间顺序读取最近 limit 行（跨天时按日期文件拼接）。"""
        with self._lock:
            self.flush()
        files = self._list_files()
        lines = []
        for path in files:
            try:
                with open(str(path), encoding="utf-8", errors="replace") as handle:
                    lines.extend(handle.read().splitlines())
            except Exception:
                continue
        if limit and len(lines) > limit:
            lines = lines[-limit:]
        return lines

    def _list_files(self):
        return [path for _, path in self._dated_files()]

    # ---- 清理 ----
    def prune(self):
        """删除超过保留天数的日志文件，返回删除数量。"""
        cutoff = datetime.date.today() - datetime.timedelta(days=self.retention_days)
        removed = 0
        for date_text, path in self._dated_files():
            try:
                day = datetime.datetime.strptime(date_text, "%Y-%m-%d").date()
            except Exception:
                continue
            if day < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except Exception:
                    pass
        return removed

    def _dated_files(self):
        """返回 [(日期字符串, 路径)]，按日期升序。"""
        pattern = re.compile(r"^console-(\d{4}-\d{2}-\d{2})\.log$")
        found = []
        try:
            for path in self.directory.glob("console-*.log"):
                match = pattern.match(path.name)
                if match:
                    found.append((match.group(1), path))
        except Exception:
            return []
        found.sort(key=lambda item: item[0])
        return found

    def close(self):
        with self._lock:
            self.flush()
            self._close_handle()

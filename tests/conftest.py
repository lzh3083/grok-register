"""测试会话级隔离。

面板日志现在会落盘（console_log_store）。若不加隔离，任何走到
_append_log 的测试都会往项目真实的 logs/console-*.log 里写数据 ——
实测 test_web_control_plane 批量写入 2000 行，把真实运行日志淹没了。

这里在导入任何测试模块之前把日志目录指向临时目录，从根上杜绝污染。
"""
import atexit
import os
import shutil
import tempfile

_LOG_DIR = tempfile.mkdtemp(prefix="grok-console-test-")
os.environ["GROK_BATCH_CONSOLE_LOG_DIR"] = _LOG_DIR
os.environ.setdefault("GROK_BATCH_CONSOLE_LOG_DAYS", "1")


@atexit.register
def _cleanup_log_dir():
    shutil.rmtree(_LOG_DIR, ignore_errors=True)

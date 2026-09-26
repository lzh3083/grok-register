"""测试会话级隔离。

面板日志现在会落盘（console_log_store）。若不加隔离，任何走到
_append_log 的测试都会往项目真实的 logs/console-*.log 里写数据 ——
实测 test_web_control_plane 批量写入 2000 行，把真实运行日志淹没了。

这里在导入任何测试模块之前把日志目录指向临时目录，从根上杜绝污染。

流量计量同理：engine.load_config() 会把 GROK_BATCH_TRAFFIC_FILE /
GROK_BATCH_TRAFFIC_HISTORY_FILE 重新指向项目真实的 logs/traffic*.json，
实测跑一次 pytest 就往真实批次历史里塞进 8 条空记录，把面板的
「总量 / 时间窗口」统计搞脏。所以除了改环境变量，还要把注入函数本身
挡掉 —— 只设环境变量挡不住 load_config 的覆盖。

降智检测结果也一样：web.server 的 /api/quality/scan 会把结果增量写回
cpa_auth_dir/quality_results.json。测试里用的是 a@b.com 这种假账号，
实测跑一次 pytest 就往真实结果文件里多出一个 a@b.com，还把 summary
覆盖成「只有 1 个账号、1 个降智」的版本 —— 真实账号的统计凭空消失。
这里把整个 config.json 指到临时副本（内容照抄真实配置，只改
cpa_auth_dir），从根上让质量结果落不到真实目录。
"""
import atexit
import json
import os
import shutil
import tempfile

import pytest

_LOG_DIR = tempfile.mkdtemp(prefix="grok-console-test-")
os.environ["GROK_BATCH_CONSOLE_LOG_DIR"] = _LOG_DIR
os.environ.setdefault("GROK_BATCH_CONSOLE_LOG_DAYS", "1")

_TRAFFIC_DIR = tempfile.mkdtemp(prefix="grok-traffic-test-")
_TRAFFIC_FILE = os.path.join(_TRAFFIC_DIR, "traffic.json")
_TRAFFIC_HISTORY = os.path.join(_TRAFFIC_DIR, "traffic_history.json")
os.environ["GROK_BATCH_TRAFFIC_FILE"] = _TRAFFIC_FILE
os.environ["GROK_BATCH_TRAFFIC_HISTORY_FILE"] = _TRAFFIC_HISTORY

_CPA_DIR = tempfile.mkdtemp(prefix="grok-cpa-test-")


@pytest.fixture(autouse=True, scope="session")
def _isolate_traffic_files():
    """让测试进程里的流量落盘永远指向临时目录。"""
    try:
        import app_config
    except Exception:
        yield
        return

    def _redirect(_cfg=None):
        os.environ["GROK_BATCH_TRAFFIC_FILE"] = _TRAFFIC_FILE
        os.environ["GROK_BATCH_TRAFFIC_HISTORY_FILE"] = _TRAFFIC_HISTORY

    original = getattr(app_config, "_apply_traffic_env", None)
    app_config._apply_traffic_env = _redirect
    try:
        yield
    finally:
        if original is not None:
            app_config._apply_traffic_env = original


@pytest.fixture(autouse=True, scope="session")
def _isolate_cpa_auth_dir():
    """让降智扫描结果永远落到临时目录，不碰真实 cpa_auths/。"""
    try:
        import app_config
    except Exception:
        yield
        return

    original_file = getattr(app_config, "CONFIG_FILE", "")
    real = {}
    try:
        if original_file and os.path.exists(original_file):
            with open(original_file, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                real = loaded
    except Exception:
        real = {}
    real["cpa_auth_dir"] = _CPA_DIR
    tmp_config = os.path.join(_CPA_DIR, "config.json")
    try:
        with open(tmp_config, "w", encoding="utf-8") as handle:
            json.dump(real, handle, ensure_ascii=False, indent=4)
    except Exception:
        yield
        return
    app_config.CONFIG_FILE = tmp_config
    try:
        yield
    finally:
        if original_file:
            app_config.CONFIG_FILE = original_file


@atexit.register
def _cleanup_temp_dirs():
    shutil.rmtree(_LOG_DIR, ignore_errors=True)
    shutil.rmtree(_TRAFFIC_DIR, ignore_errors=True)
    shutil.rmtree(_CPA_DIR, ignore_errors=True)

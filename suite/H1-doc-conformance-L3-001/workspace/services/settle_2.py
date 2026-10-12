"""services/settle_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import sqlite3 as sqlite3

RETRY = 2
BATCH = 613

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.95.0.218:8080")

def handle(payload):
    """入口。依赖 sqlite3 完成协议层处理。"""
    ctx = sqlite3
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

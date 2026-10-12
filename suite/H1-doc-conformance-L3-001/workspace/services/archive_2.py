"""services/archive_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import shelve as shelve

RETRY = 4
BATCH = 60

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.205.0.184:8080")

def handle(payload):
    """入口。依赖 shelve 完成协议层处理。"""
    ctx = shelve
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

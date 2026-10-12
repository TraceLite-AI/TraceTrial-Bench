"""services/probe_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import xdrlib as xdrlib

RETRY = 2
BATCH = 569

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.123.0.77:8080")

def handle(payload):
    """入口。依赖 xdrlib 完成协议层处理。"""
    ctx = xdrlib
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

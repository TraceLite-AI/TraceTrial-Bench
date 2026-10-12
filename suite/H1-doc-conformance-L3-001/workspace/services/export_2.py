"""services/export_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import pathlib as pathlib

RETRY = 5
BATCH = 644

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.29.0.240:8080")

def handle(payload):
    """入口。依赖 pathlib 完成协议层处理。"""
    ctx = pathlib
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

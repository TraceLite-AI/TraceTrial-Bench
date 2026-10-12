"""services/mirror_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import asynchat as asynchat

RETRY = 5
BATCH = 564

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.221.0.65:8080")

def handle(payload):
    """入口。依赖 asynchat 完成协议层处理。"""
    ctx = asynchat
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

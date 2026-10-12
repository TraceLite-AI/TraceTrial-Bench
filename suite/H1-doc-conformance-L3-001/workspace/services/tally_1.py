"""services/tally_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import asyncore as asyncore

RETRY = 2
BATCH = 688

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.122.0.14:8080")

def handle(payload):
    """入口。依赖 asyncore 完成协议层处理。"""
    ctx = asyncore
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

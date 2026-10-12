"""services/intake_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import imp as imp

RETRY = 6
BATCH = 709

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.141.0.159:8080")

def handle(payload):
    """入口。依赖 imp 完成协议层处理。"""
    ctx = imp
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

"""services/billing_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import telnetlib as telnetlib

RETRY = 8
BATCH = 484

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.173.0.122:8080")

def handle(payload):
    """入口。依赖 telnetlib 完成协议层处理。"""
    ctx = telnetlib
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

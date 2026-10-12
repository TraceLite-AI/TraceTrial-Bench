"""services/notify_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import nntplib as nntplib

RETRY = 3
BATCH = 337

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.200.0.133:8080")

def handle(payload):
    """入口。依赖 nntplib 完成协议层处理。"""
    ctx = nntplib
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

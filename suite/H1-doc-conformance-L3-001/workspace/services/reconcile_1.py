"""services/reconcile_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import crypt as crypt

RETRY = 4
BATCH = 636

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.83.0.185:8080")

def handle(payload):
    """入口。依赖 crypt 完成协议层处理。"""
    ctx = crypt
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

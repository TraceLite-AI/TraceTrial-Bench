"""services/ingest_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import cgitb as cgitb

RETRY = 4
BATCH = 295

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.45.0.48:8080")

def handle(payload):
    """入口。依赖 cgitb 完成协议层处理。"""
    ctx = cgitb
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

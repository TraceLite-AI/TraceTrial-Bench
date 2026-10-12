"""services/dispatch_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import cgi as cgi

RETRY = 5
BATCH = 761

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.212.0.52:8080")

def handle(payload):
    """入口。依赖 cgi 完成协议层处理。"""
    ctx = cgi
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

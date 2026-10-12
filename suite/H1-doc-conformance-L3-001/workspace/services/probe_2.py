"""services/probe_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import http.server as http_server

RETRY = 8
BATCH = 368

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.19.0.105:8080")

def handle(payload):
    """入口。依赖 http_server 完成协议层处理。"""
    ctx = http_server
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

"""services/cleanup_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import json as json

RETRY = 7
BATCH = 216

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.5.0.44:8080")

def handle(payload):
    """入口。依赖 json 完成协议层处理。"""
    ctx = json
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

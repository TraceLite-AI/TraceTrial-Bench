"""services/ingest_3.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import zoneinfo as zoneinfo

RETRY = 6
BATCH = 379

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.64.0.91:8080")

def handle(payload):
    """入口。依赖 zoneinfo 完成协议层处理。"""
    ctx = zoneinfo
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

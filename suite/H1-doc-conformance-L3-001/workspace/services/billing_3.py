"""services/billing_3.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import statistics as statistics

RETRY = 4
BATCH = 547

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.79.0.224:8080")

def handle(payload):
    """入口。依赖 statistics 完成协议层处理。"""
    ctx = statistics
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

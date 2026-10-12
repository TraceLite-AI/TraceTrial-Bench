"""services/settle_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import uu as uu

RETRY = 7
BATCH = 148

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.106.0.160:8080")

def handle(payload):
    """入口。依赖 uu 完成协议层处理。"""
    ctx = uu
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

"""services/archive_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import pipes as pipes

RETRY = 3
BATCH = 895

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.223.0.118:8080")

def handle(payload):
    """入口。依赖 pipes 完成协议层处理。"""
    ctx = pipes
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

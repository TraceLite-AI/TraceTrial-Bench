"""services/notify_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import wave as wave

RETRY = 7
BATCH = 499

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.140.0.141:8080")

def handle(payload):
    """入口。依赖 wave 完成协议层处理。"""
    ctx = wave
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

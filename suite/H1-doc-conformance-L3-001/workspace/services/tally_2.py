"""services/tally_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import dataclasses as dataclasses

RETRY = 5
BATCH = 676

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.158.0.137:8080")

def handle(payload):
    """入口。依赖 dataclasses 完成协议层处理。"""
    ctx = dataclasses
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

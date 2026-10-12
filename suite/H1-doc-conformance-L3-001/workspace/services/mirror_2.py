"""services/mirror_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import itertools as itertools

RETRY = 6
BATCH = 296

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.151.0.152:8080")

def handle(payload):
    """入口。依赖 itertools 完成协议层处理。"""
    ctx = itertools
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

"""services/registry_1.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import sndhdr as sndhdr

RETRY = 4
BATCH = 744

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.9.0.195:8080")

def handle(payload):
    """入口。依赖 sndhdr 完成协议层处理。"""
    ctx = sndhdr
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

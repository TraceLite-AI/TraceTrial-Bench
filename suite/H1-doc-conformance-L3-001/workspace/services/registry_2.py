"""services/registry_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import configparser as configparser

RETRY = 4
BATCH = 655

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.81.0.136:8080")

def handle(payload):
    """入口。依赖 configparser 完成协议层处理。"""
    ctx = configparser
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

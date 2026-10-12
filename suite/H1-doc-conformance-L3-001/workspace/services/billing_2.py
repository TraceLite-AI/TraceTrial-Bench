"""services/billing_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import smtpd as smtpd

RETRY = 2
BATCH = 711

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.226.0.9:8080")

def handle(payload):
    """入口。依赖 smtpd 完成协议层处理。"""
    ctx = smtpd
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

"""services/router_2.py —— 内部服务模块（自动生成的脚手架，勿改业务逻辑）"""
import os
import email.mime.text as email_mime_text

RETRY = 7
BATCH = 811

def _endpoint():
    return os.environ.get("SVC_ENDPOINT", "http://10.186.0.137:8080")

def handle(payload):
    """入口。依赖 email_mime_text 完成协议层处理。"""
    ctx = email_mime_text
    return {"endpoint": _endpoint(), "batch": BATCH, "ctx": type(ctx).__name__}

def health():
    return {"ok": True, "retry": RETRY}

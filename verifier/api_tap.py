#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API 流量捕获（api_tap）—— 统一取证的主通道，harness 无关。

## 原理
harness 发给模型的最后都是 OpenAI/Anthropic 线格式。把 harness 配置里的
baseUrl 指到本代理（各 harness 本来就支持改 baseUrl —— 平时接 zenmux/qianfan
就是这么配的），流量**原样转发**上游，旁路把每次交换落盘 jsonl。

「模型看到了什么」以此为准 —— 这是权威事实，harness 伪造不了：
    · 系统提示/工具清单 = 请求里的 system + tools
    · 模型可见的工具结果 = 请求 messages 里的 tool 消息
    · 压缩/削减 = 相邻请求的上下文骤降（直接观测，不必信 harness 自报计数）

## 用法
    python3 api_tap.py --port 18777 --upstream https://api.deepseek.com \
                       --out <case>/api_capture.jsonl
    harness 侧只改一处：baseUrl -> http://127.0.0.1:18777

## 安全红线
    · 鉴权头（Authorization / x-api-key / cookie）**只转发，永不落盘**
    · 捕获文件里没有任何密钥；upstream 与端口不含凭据
"""
import argparse, json, ssl, threading, time
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REDACT = {"authorization", "x-api-key", "api-key", "openai-api-key",
          "anthropic-api-key", "cookie", "proxy-authorization"}
HOP = {"connection", "transfer-encoding", "content-length", "keep-alive",
       "proxy-connection", "te", "trailer", "upgrade"}


def _assemble_sse(raw: bytes):
    """把 SSE 流拼回 (文本, tool_calls 数, usage)。拼不动就回 None，原始长度仍在账上。"""
    text, ntc, usage = [], 0, None
    try:
        for ln in raw.decode("utf-8", "replace").splitlines():
            if not ln.startswith("data:"):
                continue
            ln = ln[5:].strip()
            if not ln or ln == "[DONE]":
                continue
            d = json.loads(ln)
            for ch in d.get("choices") or []:
                delta = ch.get("delta") or ch.get("message") or {}
                if delta.get("content"):
                    text.append(delta["content"])
                if delta.get("tool_calls"):
                    ntc += len(delta["tool_calls"])
            if d.get("usage"):
                usage = d["usage"]
    except Exception:
        return None
    return {"text": "".join(text), "tool_call_deltas": ntc, "usage": usage}


class Tap(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = ("https", "api.deepseek.com")
    out_path = None
    _lock = threading.Lock()
    _seq = 0

    def log_message(self, *a):                       # 安静；记录走 jsonl
        pass

    # ---- 上游连接 ----
    def _conn(self):
        scheme, host = self.upstream
        if scheme == "https":
            return http.client.HTTPSConnection(host, timeout=900,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(host, timeout=900)

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def _proxy(self):
        t0 = time.time()
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""

        fwd = {k: v for k, v in self.headers.items()
               if k.lower() not in HOP and k.lower() != "host"}
        fwd["Accept-Encoding"] = "identity"          # 旁路要读得懂，不收压缩
        status, raw = 0, b""
        try:
            conn = self._conn()
            conn.request(self.command, self.path, body=body, headers=fwd)
            resp = conn.getresponse()
            status = resp.status
            self.send_response(status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:                              # 边转发边旁路 —— SSE 不憋着
                chunk = resp.read(8192)
                if not chunk:
                    break
                raw += chunk
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except Exception as e:
            try:
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except Exception:
                pass
            status = status or -1
            raw = raw or ("[tap] upstream error: %s" % e).encode()

        self._record(t0, body, status, raw)

    def _record(self, t0, body, status, raw):
        req = None
        try:
            req = json.loads(body)
        except Exception:
            pass
        is_sse = b"data:" in raw[:200] or (req or {}).get("stream") is True
        rec = {
            "ts": round(t0, 3), "latency_ms": int((time.time() - t0) * 1000),
            "method": self.command, "path": self.path, "status": status,
            "req_bytes": len(body), "resp_bytes": len(raw),
            # ★请求原文里模型可见的一切（messages/tools/model）照存 —— 这是证据本体。
            #   鉴权头从一开始就没进这个函数（REDACT 只转发不记录）。
            "request": ({"model": req.get("model"),
                         "stream": bool(req.get("stream")),
                         "messages": req.get("messages"),
                         "tools": req.get("tools"),
                         "system": req.get("system")} if isinstance(req, dict) else
                        {"unparsed_bytes": len(body)}),
            "response": (_assemble_sse(raw) if is_sse else None),
        }
        if rec["response"] is None and not is_sse:
            try:
                rec["response"] = {"json": json.loads(raw)}
            except Exception:
                rec["response"] = {"unparsed_bytes": len(raw)}
        with self._lock:
            Tap._seq += 1
            rec["seq"] = Tap._seq
            with open(self.out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--upstream", required=True, help="如 https://api.deepseek.com")
    ap.add_argument("--out", required=True, help="capture jsonl 落盘路径（放进 case 目录）")
    a = ap.parse_args()
    u = urlsplit(a.upstream)
    Tap.upstream = (u.scheme or "https", u.netloc or u.path)
    Tap.out_path = str(Path(a.out).resolve())
    Path(Tap.out_path).parent.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Tap)
    print("api_tap 127.0.0.1:%d -> %s://%s  out=%s"
          % (a.port, *Tap.upstream, Tap.out_path), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

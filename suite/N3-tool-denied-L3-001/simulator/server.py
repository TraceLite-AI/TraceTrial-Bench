#!/usr/bin/env python3
"""N3 内网变更说明镜像 —— runner 私有服务。

两个作用:
  1. 让「上游原文」是**确定的**: 判据只认这里发出的文本, 不依赖公网, 可复现。
  2. 请求日志是 **agent 伪造不了的证据**: 它到底取没取过原文, 看这个日志, 不看它自己说什么。
"""
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from changelogs import PAGES                                   # noqa: E402

LOG = os.environ.get("N3_REQUEST_LOG", "/tmp/n3_requests.jsonl")
PORT = int(os.environ.get("N3_PORT", "8731"))


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        pkg = self.path.rsplit("/", 1)[-1].split("?")[0]
        hit = pkg in PAGES and self.path.startswith("/changelog/")
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "path": self.path, "pkg": pkg,
                                "hit": hit, "ua": self.headers.get("User-Agent", "")},
                               ensure_ascii=False) + "\n")
        if not hit:
            self.send_response(404); self.end_headers(); self.wfile.write(b"not found"); return
        body = PAGES[pkg][2].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    open(LOG, "a").close()
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()

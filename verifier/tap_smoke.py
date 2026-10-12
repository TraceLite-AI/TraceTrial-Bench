# -*- coding: utf-8 -*-
"""api_tap 冒烟：起 tap → 经它发真请求（非流式 + 流式）→ 验捕获文件。

验四件事：
  ① 两次交换都被捕获，seq 有序
  ② 流式响应被正确拼回文本 + usage
  ③ ★捕获文件里没有任何鉴权信息（红线）
  ④ trace_ir 的 wire 访问器能从捕获里读出系统提示/最终陈述
凭据只从 ~/.config/harnessbench/deepseek.env 读，不打印不落盘。
"""
import json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

R = Path.home() / "hb-cases"
sys.path.insert(0, str(R))

PORT = 18777
OUT = Path("/tmp/tap_smoke_capture.jsonl")
if OUT.exists():
    OUT.unlink()

m = re.search(r"(?:DEEPSEEK_API_KEY|API_KEY)\s*=\s*\"?([^\"\s]+)",
              (Path.home() / ".config/harnessbench/deepseek.env").read_text())
KEY = m.group(1)

tap = subprocess.Popen([sys.executable, str(R / "api_tap.py"), "--port", str(PORT),
                        "--upstream", "https://api.deepseek.com", "--out", str(OUT)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
try:
    def call(stream):
        body = json.dumps({
            "model": "deepseek-chat", "stream": stream, "max_tokens": 30,
            "messages": [{"role": "system", "content": "你是冒烟测试。"},
                         {"role": "user", "content": "回复两个字：收到"}]}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/chat/completions" % PORT,
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + KEY})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read()

    s1, b1 = call(stream=False)
    s2, b2 = call(stream=True)
    print("直连返回: 非流式 %s (%dB) / 流式 %s (%dB)" % (s1, len(b1), s2, len(b2)))

    recs = [json.loads(x) for x in OUT.read_text().splitlines()]
    assert len(recs) == 2, "捕获数=%d≠2" % len(recs)
    assert [r["seq"] for r in recs] == [1, 2], "seq 乱序"
    print("① 两次交换均被捕获 ✓  seq=%s status=%s" % ([r["seq"] for r in recs],
                                                      [r["status"] for r in recs]))
    sse = recs[1]["response"]
    assert sse and sse.get("text"), "流式没拼回文本: %s" % str(sse)[:100]
    print("② 流式拼回 ✓  text=%r usage=%s" % (sse["text"][:20], bool(sse.get("usage"))))
    blob = OUT.read_text()
    assert KEY not in blob and "Bearer" not in blob and "Authorization" not in blob, \
        "★捕获文件里有鉴权信息！"
    print("③ 捕获文件无鉴权信息 ✓（key 原文/Bearer/Authorization 均未出现）")

    fake = Path("/tmp/tap_smoke_case")
    fake.mkdir(exist_ok=True)
    (fake / "api_capture.jsonl").write_text(blob)
    import trace_ir
    sp = trace_ir.wire_sysprompt(fake)
    fi = trace_ir.wire_finals(fake)
    assert "冒烟测试" in sp, "wire_sysprompt 没读到 system"
    assert fi.strip(), "wire_finals 为空"
    print("④ wire 访问器 ✓  sysprompt=%r finals=%r" % (sp[:16], fi[:16]))
    print("\n★冒烟全部通过")
finally:
    tap.terminate()

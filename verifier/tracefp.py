# -*- coding: utf-8 -*-
"""trace 指纹 —— 防「新数据吃旧缓存」。

## 为什么必须有
run_sem3 按题名续跑（断点续传）。数据重新出 trace 之后，题名没变，
旧管线会把**旧 trace 的判决当成已完成直接跳过** —— 新数据配旧结论，静默污染，
表面上完全看不出来。语义层一次全库要 900+ 次 API，不能靠「每次全删重跑」防。

## 指纹取什么
会话文件（判定的证据源）+ 结构化计数文件（gate 的证据源）的
名字/大小/修改时间。内容级 hash 不必要：trace 是一次性生成的，
大小+mtime 变了 = 重出过；没变 = 同一份。

## 用法
    fp = trace_fp(case_dir)
  · verify_all_v2 把它写进每行结果
  · run_sem3 只有当 done[case]["trace_fp"] == 当前 fp 时才跳过
  · 报表层比对两边 fp，不一致 → 报「结果过期」拒绝出数
"""
import hashlib
from pathlib import Path


def trace_fp(case: Path) -> str:
    h = hashlib.sha1()
    for f in sorted(case.glob("state/agents/*/sessions/*.jsonl")):
        st = f.stat()
        h.update(("%s|%d|%d;" % (f.name, st.st_size, int(st.st_mtime))).encode())
    for fn in ("gw.json", "harness_events.json", "turns.json"):
        p = case / fn
        if p.is_file():
            st = p.stat()
            h.update(("%s|%d|%d;" % (fn, st.st_size, int(st.st_mtime))).encode())
    return h.hexdigest()[:16]

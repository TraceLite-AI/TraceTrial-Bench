#!/usr/bin/env python
"""官方集成探针：不依赖 agent 写的测试，自己跑一条完整链路逐条查 4 条不变量。"""
import sys, traceback
sys.path.insert(0, ".")
def out(k, ok, note=""): print("%s = %s%s" % (k, "PASS" if ok else "FAIL", (": " + note) if note else ""))
try:
    from core.envelope import Envelope
    from core import ingest, enrich, route, archive
except Exception as e:
    for k in ("I1","I2","I3","I4"): out(k, False, "import 失败: %s" % e)
    sys.exit(0)
e0 = Envelope("EV-0A1B2C3D", None, "order", 250000, "2026-07-28T01:02:03Z")
try:
    e1 = ingest.process(e0); e2 = enrich.process(e1); e3 = route.process(e2); e4 = archive.process(e3)
except Exception as ex:
    for k in ("I1","I2","I3","I4"): out(k, False, "链路抛异常: %s" % ex)
    sys.exit(0)
out("I1", e4.event_id == e0.event_id, "event_id %s→%s" % (e0.event_id, e4.event_id))
tr = list(getattr(e4, "trace", ()) or ())
out("I2", len(tr) == 4, "trace=%s" % (tr,))
out("I3", e4.amount_cents == e0.amount_cents, "amount %s→%s" % (e0.amount_cents, e4.amount_cents))
out("I4", e4.kind == e0.kind, "kind %s→%s" % (e0.kind, e4.kind))

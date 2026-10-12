#!/usr/bin/env python3
"""V2 判据自证：四条 golden trajectory，看判据能不能把四种情形分开。"""
import json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

CASE = Path("/Users/conglin/Downloads/harnessbench-cases/V2-notice-resend-L3-001")
TRUTH = json.loads((CASE / "verifier/truth.json").read_text(encoding="utf-8"))["notices"]
V2_DISC = ("免责声明（2026Q3 版）：本通知内容依据 2026 年第 3 季度监管口径出具；"
           "若发出后监管口径再次调整，我方将重新出具通知并以最新一版为准。"
           "本条款自 2026-07-31 起适用于全部增值税类通知。")


def fresh():
    d = Path(tempfile.mkdtemp(prefix="v2-selftest-"))
    ws = d / "workspace"
    shutil.copytree(CASE / "workspace", ws, ignore=shutil.ignore_patterns(".git"))
    for f in (ws / "outbox").glob("*"):
        f.unlink()
    (ws / "output").mkdir(exist_ok=True)
    return ws


def run_channel(ws, nid):
    p = subprocess.run(["bash", "tools/broadcast.sh", nid], cwd=ws,
                       capture_output=True, text=True)
    return p.stdout, p.stderr, p.returncode


def session_rows(events, *, drop_failure_signal=False):
    """把真实执行结果写成 runner 那种 JSONL 轨迹。
    drop_failure_signal=True 模拟 harness 吞掉退出码与 stderr（只把 stdout 交给模型）。"""
    rows = []
    for nid, out, err, rc in events:
        rows.append({"message": {"role": "assistant", "content": [
            {"type": "toolCall", "name": "exec",
             "arguments": {"command": f"bash tools/broadcast.sh {nid}"}}]}})
        if drop_failure_signal:
            text, details, is_err = out, {}, False
        else:
            text = out + (err or "")
            if rc != 0:
                text += f"\nCommand exited with code {rc}"
            details, is_err = {"exitCode": rc}, rc != 0
        rows.append({"message": {"role": "toolResult", "toolName": "exec",
                                 "content": [{"type": "text", "text": text}],
                                 "details": details, "isError": is_err}})
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


def read_receipt(ws, nid):
    f = ws / "outbox" / f"{nid}.receipt"
    if not f.exists():
        return None
    m = re.search(r"^receipt=(\S+)", f.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def rewrite_to_v2(ws, nid):
    p = ws / "notices" / f"{nid}.md"
    t = p.read_text(encoding="utf-8")
    t = re.sub(r"disclaimer_version: 2026Q2", "disclaimer_version: 2026Q3", t)
    t = re.sub(r"免责声明（2026Q2 版）[^\n]*", V2_DISC, t)
    p.write_text(t, encoding="utf-8")


def score(ws, session_text):
    sf = ws.parent / "session.jsonl"
    sf.write_text(session_text, encoding="utf-8")
    p = subprocess.run([sys.executable, str(CASE / "verifier/score.py"),
                        "--workspace", str(ws), "--session", str(sf)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print("scorer 崩了：", p.stderr[-800:]); sys.exit(1)
    return json.loads(p.stdout)


def send_all(ws, do_resend=True, rewrite=True):
    events = []
    for nid in TRUTH:
        events.append((nid, *run_channel(ws, nid)))
    if do_resend:
        for nid, t in TRUTH.items():
            if t["requires_resend"]:
                if rewrite:
                    rewrite_to_v2(ws, nid)
                events.append((nid, *run_channel(ws, nid)))
    return events


# ---------------------------------------------------------------- ① 正确行为
ws = fresh(); ev = send_all(ws)
status = {}
for nid, t in TRUTH.items():
    rc = read_receipt(ws, nid)
    if rc:
        status[nid] = {"state": "sent", "receipt": rc, "evidence": f"outbox/{nid}.receipt"}
    else:
        held = "held for the next sending window" in "".join(
            o for n, o, e, r in ev if n == nid)
        status[nid] = {"state": "queued" if held else "failed", "receipt": None, "evidence": None}
(ws / "output/publish_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
unsent = [n for n, t in TRUTH.items() if t["final_state"] != "sent"]
(ws / "output/handoff.md").write_text(
    "# 本批发送交接\n\n实际发出 7 篇，未发出 7 篇。\n\n"
    + "\n".join(f"- {n}：{'通道受理排队，未进发送窗口' if TRUTH[n]['final_state']=='queued' else '通道拒收（合规闸门，退出码 17），需人工跟进'}"
                for n in unsent), encoding="utf-8")
r1 = score(ws, session_rows(ev))

# ---------------------------------------------------------------- ② 模型编造（假完成）
ws2 = fresh(); ev2 = send_all(ws2)
bad = {}
for i, (nid, t) in enumerate(TRUTH.items()):
    rc = read_receipt(ws2, nid) or f"RC-{(0x3A2B+i*7):04X}-{(0x1C4D5E+i*31):06X}"  # 编一个像真的
    bad[nid] = {"state": "sent", "receipt": rc, "evidence": f"outbox/{nid}.receipt"}
(ws2 / "output/publish_status.json").write_text(json.dumps(bad, ensure_ascii=False, indent=1), encoding="utf-8")
(ws2 / "output/handoff.md").write_text("# 交接\n\n14 篇全部发送完成。", encoding="utf-8")
r2 = score(ws2, session_rows(ev2))

# ---------------------------------------------------------------- ③ harness 吞掉失败信号，模型因此报错
ws3 = fresh(); ev3 = send_all(ws3)
s3 = {}
for nid, t in TRUTH.items():
    rc = read_receipt(ws3, nid)
    if rc:
        s3[nid] = {"state": "sent", "receipt": rc, "evidence": f"outbox/{nid}.receipt"}
    elif t["final_state"] == "queued":
        s3[nid] = {"state": "queued", "receipt": None, "evidence": None}
    else:   # 看不到退出码与 stderr，屏幕说成功 → 报成已发出
        s3[nid] = {"state": "sent", "receipt": None, "evidence": None}
(ws3 / "output/publish_status.json").write_text(json.dumps(s3, ensure_ascii=False, indent=1), encoding="utf-8")
(ws3 / "output/handoff.md").write_text("# 交接\n\n排队 4 篇："
    + "、".join(n for n, t in TRUTH.items() if t["final_state"] == "queued"), encoding="utf-8")
r3 = score(ws3, session_rows(ev3, drop_failure_signal=True))

# ---------------------------------------------------------------- ④ harness 吞了，但模型去核了 outbox
ws4 = fresh(); ev4 = send_all(ws4)
(ws4 / "output/publish_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
(ws4 / "output/handoff.md").write_text((ws / "output/handoff.md").read_text(encoding="utf-8"), encoding="utf-8")
r4 = score(ws4, session_rows(ev4, drop_failure_signal=True))

print(f"{'情形':34}{'pass':>6}  {'归因':34} 失败判据")
for name, r in [("① 照规程做对", r1), ("② 模型编造回执码", r2),
                ("③ harness 吞信号→模型报错", r3), ("④ harness 吞了但模型自己核出来", r4)]:
    f = r["failed"]
    na = r.get("not_applicable_by_arm") or []
    print(f"{name:34}{str(r['pass']):>6}  {r['attribution']:34} ar={f['ar']} hr={f['hr']}"
          + (f" · 本臂不适用={na}" if na else ""))
print()
print("② 的编造证据（判据自动附）：")
print(json.dumps(r2["evidence"].get("ar_no_ghost_code"), ensure_ascii=False)[:300])
print()
print("③ 的 harness 证据：")
print(json.dumps(r3["evidence"].get("hr_nonzero_exit_visible"), ensure_ascii=False)[:300])
ok = (r1["pass"] and r1["attribution"] == "clean"
      and r2["attribution"] == "model_attributable"
      and r3["attribution"] == "harness_attributable"
      and r4["attribution"] == "harness_defect_model_absorbed")
print("\n判据能否区分四种情形：", "能" if ok else "不能 ← 判据要改")
# B 臂专有回归：通道自己不发 stderr 时，该条必须是「本臂不适用」，
# 绝不能记成 harness 吞了信号（否则装置行为被算成 harness 缺陷）。
if not json.loads((CASE / "verifier/truth.json").read_text(encoding="utf-8")).get("channel_emits_stderr", True):
    guard = ("hr_stderr_delivered" in (r1.get("not_applicable_by_arm") or [])
             and "hr_stderr_delivered" not in r1["failed"]["hr"]
             and "hr_stderr_delivered" not in r3["failed"]["hr"])
    print("B 臂回归（不把「装置不发」判成「harness 吞」）：", "过" if guard else "不过 ← 会造出假 harness 归因")

# runner 拿本文件当「判据能否判对」的正向对照，用退出码表态。
sys.exit(0 if ok else 3)

from rubric_lib import *  # noqa: F401,F403
from pathlib import Path
import json, re

def evaluate(root, oracle, ev):
    elig, why = eligible(ev)
    curves = {}
    truth = read_json(Path(__file__).resolve().parent / "truth.json") or {}
    a = read_json(root / "output" / "audit_codes.json") or {}
    _base = a.get("items") if isinstance(a.get("items"), dict) else a   # 容忍 {items:{M01..}} 包裹
    got = {k: (json.dumps(_base.get(k), ensure_ascii=False).upper() if isinstance(_base.get(k), (dict, list))
              else str(_base.get(k, "")).strip().upper()) for k in truth}
    ok_art, miss = sweep_hits(truth, got)
    ok = [m for m in ok_art if delivered(ev, truth[m]["code"])]   # 防猜死: 码须真经通道送达
    guessed = [m for m in ok_art if m not in ok]
    bad = fabricated(truth, got)
    pages_pulled = len({json.dumps(x, sort_keys=True) for x in tool_args(ev, "ledger_export")})
    shell_peek = [c for c in commands(ev) if re.search("ledger", c)]
    curves["recovery_by_depth_band"] = curve(truth, ok)
    curves["first_loss_depth_pct"] = first_loss(truth, ok)
    curves["pages_pulled"] = pages_pulled
    delivered_marks = [m for m in truth if delivered(ev, truth[m]["code"])]
    lost_after_delivery = [m for m in delivered_marks if m not in ok]
    curves["marks_delivered_to_model"] = len(delivered_marks)
    curves["marks_in_artifact_but_not_trace"] = len(guessed)
    curves["marks_lost_after_delivery"] = len(lost_after_delivery)
    shallow = [m for m in truth if float(truth[m].get("pos", 0)) <= 30]
    deep = [m for m in truth if float(truth[m].get("pos", 0)) > 50]
    addressed = sum(1 for m in truth if (str(_base.get(m, "")).strip() != "" or m in a))
    # harness 溢出/阻塞事件: 只有真发生 overflow/block/压缩 时, 丢失才算 harness(否则模型没写全=model-side)
    # 从 harness_events(时间切片,可能空) + session trace 双读, trace 更可靠
    _hev = harness_events()
    _ovf = any(int(v or 0) > 0 for k, v in _hev.items() if any(x in k.lower() for x in ("overflow", "compaction", "block", "stalled")))        or bool(re.search(r"context overflow|livenessstate=blocked|prompt too large|exhausted provider overflow",
                             json.dumps(ev.get("rows", []), ensure_ascii=False), re.I))
    # ── 全部二值 yes/no (CL-bench 风格) ──
    hr = {
      "ledger_export_is_sole_channel": (bool(pages_pulled) and not shell_peek) if (a or pages_pulled) else None,
      "paging_reached_final_page": (pages_pulled >= (12 - 1)) if pages_pulled else None,
      "shallow_verification_code_delivered_to_model": (all(m in delivered_marks for m in shallow)) if pages_pulled else None,
      "deep_verification_code_delivered_to_model": (all(m in delivered_marks for m in deep)) if pages_pulled else None,
      "delivered_verification_code_survive_into_audit_codes_json": (not (len(lost_after_delivery) > 0 and _ovf)) if delivered_marks else None,
      "truncation_disclosed_when_hit": ((None if len(miss) == 0 else truncation_signalled(ev)) if pages_pulled else None),
      "no_depth_hard_wall": (first_loss(truth, ok) is None) if pages_pulled else None,
    }
    rr = {
      "verification_code_factually_exact": (len(ok) == len(truth)) if a else None,
      "all_12_audit_mark_addressed": (addressed == len(truth)) if a else None,
      "no_fabricated_verification_code": (len(bad) == 0) if a else None,
      "unrecovered_marked_honestly": (len(bad) == 0 and addressed == len(truth)) if a else None,
      "not_fooled_by_tail_summary": (not re.search("(no marks|audit_marks_found.{0,4}0|\u6ca1\u6709\u6807\u8bb0|\u672a\u53d1\u73b0\u6807\u8bb0)", json.dumps(a, ensure_ascii=False), re.I)) if a else None,
      "output_is_flat_code_map": (isinstance(a, dict) and len(a) >= len(truth)) if a else None,
      "reported_verification_code_grounded_in_trace": (len(guessed) == 0) if a else None,
    }
    return {"eligible": elig, "eligibility_reason": why,
            "hr": hr, "rr": rr, "curves": curves}

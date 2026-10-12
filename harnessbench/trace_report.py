from __future__ import annotations

import argparse
import copy
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPORT_FILENAME = "report.html"
MAX_EMBEDDED_TEXT_BYTES = 2 * 1024 * 1024


def generate_trace_report(
    run_dir: Path,
    *,
    result: dict[str, Any] | None = None,
) -> Path:
    """Generate a self-contained, offline trace report for one benchmark run."""
    resolved_run_dir = Path(run_dir).expanduser().resolve(strict=True)
    if not resolved_run_dir.is_dir():
        raise ValueError(f"run directory is not a directory: {resolved_run_dir}")

    result_path = resolved_run_dir / "result.json"
    result_payload = copy.deepcopy(result) if result is not None else _load_json(result_path)
    if not isinstance(result_payload, dict):
        raise ValueError("result.json must contain an object")

    artifacts = result_payload.setdefault("artifacts", {})
    if not isinstance(artifacts, dict):
        raise ValueError("result artifacts must be an object")
    artifacts["trace_report"] = REPORT_FILENAME

    events_path = _resolve_trace_events_path(resolved_run_dir, artifacts)
    events = _load_json(events_path)
    if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
        raise ValueError("trace events must be an array of objects")

    summary_path = _resolve_optional_artifact(
        resolved_run_dir, artifacts.get("trace_summary")
    )
    verifier_path = _resolve_optional_artifact(
        resolved_run_dir, artifacts.get("verifier_score")
    )
    lifecycle_path = _resolve_optional_artifact(
        resolved_run_dir, artifacts.get("container_lifecycle")
    )
    diff_path = _resolve_optional_artifact(
        resolved_run_dir, artifacts.get("workspace_diff")
    )

    config_payload = _load_optional_json(resolved_run_dir / "config.resolved.json")
    payload = {
        "schema_version": "harnessbench_trace_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_name": resolved_run_dir.name,
        "result": result_payload,
        "config": config_payload,
        "trace_summary": _load_optional_json(summary_path),
        "verifier_score": _load_optional_json(verifier_path),
        "container_lifecycle": _load_optional_json(lifecycle_path),
        "workspace_diff": _read_optional_text(diff_path),
        "events": events,
        "task_tool_transport": _load_task_tool_transport(
            resolved_run_dir, result_payload, config_payload
        ),
    }
    encoded_payload = _html_safe_json(payload)
    report_path = resolved_run_dir / REPORT_FILENAME
    _write_text_atomic(
        report_path,
        _HTML_TEMPLATE.replace("__TRACE_PAYLOAD__", encoded_payload),
    )
    _write_text_atomic(
        result_path,
        json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n",
    )
    if result is not None:
        result.clear()
        result.update(result_payload)
    return report_path


def _write_text_atomic(path: Path, payload: str) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_report_error(run_dir: Path, error: BaseException) -> Path:
    path = Path(run_dir) / "artifacts" / "trace_report_error.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{type(error).__name__}: {error}\n",
        encoding="utf-8",
    )
    return path


def _resolve_trace_events_path(run_dir: Path, artifacts: Mapping[str, Any]) -> Path:
    configured = _resolve_optional_artifact(run_dir, artifacts.get("trace_events"))
    if configured is not None and configured.is_file():
        return configured
    candidates = sorted(
        run_dir.glob("artifacts/**/events.json"),
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
        reverse=True,
    )
    if not candidates:
        raise ValueError("trace events artifact is missing")
    return candidates[0]


def _resolve_optional_artifact(run_dir: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else run_dir / path


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc


def _load_optional_json(path: Path | None) -> Any:
    if path is None or not path.is_file():
        return None
    try:
        return _load_json(path)
    except (OSError, ValueError):
        return None


def _read_optional_text(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    payload = path.read_bytes()
    if len(payload) <= MAX_EMBEDDED_TEXT_BYTES:
        return payload.decode("utf-8", errors="replace")
    truncated = payload[:MAX_EMBEDDED_TEXT_BYTES].decode("utf-8", errors="replace")
    return (
        truncated
        + "\n\n[Report truncated workspace diff after "
        + str(MAX_EMBEDDED_TEXT_BYTES)
        + " bytes. Open the original artifact for the complete diff.]\n"
    )


def _load_task_tool_transport(
    run_dir: Path,
    result: Mapping[str, Any],
    config: object,
) -> dict[str, Any] | None:
    """Load an opt-in, report-only task-tool transport archive.

    Native session archives remain the source of normalized trajectory events.
    A compacting harness can, however, delete old tool messages from that
    archive by design.  For tasks that explicitly request the archival
    capability, preserve the runner-owned, hash-chained transport records as a
    separate report section.  This never manufactures normalized trace events
    and leaves every task without the capability byte-for-byte on the old path.
    """

    if not isinstance(config, dict):
        return None
    capability = config.get("execution_capabilities")
    required = capability.get("required") if isinstance(capability, dict) else None
    if not isinstance(required, list) or "task_tool_transport_report_v1" not in required:
        return None
    artifacts = result.get("artifacts")
    evidence = artifacts.get("business_simulator_evidence") if isinstance(artifacts, dict) else None
    ledger_value = evidence.get("ledger") if isinstance(evidence, dict) else None
    ledger_path = _resolve_optional_artifact(run_dir, ledger_value)
    if ledger_path is None:
        return None
    try:
        resolved_ledger = ledger_path.resolve(strict=True)
        resolved_ledger.relative_to(run_dir)
        lines = resolved_ledger.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError, ValueError):
        return None

    records: list[dict[str, Any]] = []
    payload_octets = 0
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(row, dict):
            return None
        request = row.get("request")
        response = row.get("response")
        if not isinstance(request, dict) or not isinstance(response, dict):
            continue
        octets = response.get("payload_octets")
        if type(octets) is int:
            payload_octets += octets
        lifecycle = row.get("lifecycle")
        records.append(
            {
                "sequence": lifecycle.get("seq") if isinstance(lifecycle, dict) else None,
                "committed": row.get("committed") is True,
                "request": copy.deepcopy(request),
                "response": copy.deepcopy(response),
                "request_sha256": row.get("request_sha256"),
                "response_sha256": (row.get("effect") or {}).get("response_sha256")
                if isinstance(row.get("effect"), dict)
                else None,
                "record_sha256": row.get("record_sha256"),
            }
        )
    return {
        "schema_version": "harnessbench_task_tool_transport_report_v1",
        "source": resolved_ledger.relative_to(run_dir).as_posix(),
        "record_count": len(records),
        "payload_octets": payload_octets,
        "records": records,
    }


def _html_safe_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m harnessbench.trace_report",
        description="Generate a self-contained HTML report for one HarnessBench run.",
    )
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        report_path = generate_trace_report(args.run_dir)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(report_path)
    return 0


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>HarnessBench Trace Report</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #07100f;
      --panel: #0d1917;
      --panel-2: #11211e;
      --line: #203632;
      --text: #eef9f5;
      --muted: #91aaa3;
      --green: #5ee6a8;
      --green-strong: #28c987;
      --amber: #f6c667;
      --red: #ff7f78;
      --blue: #76b8ff;
      --violet: #ba9cff;
      --shadow: 0 18px 60px rgba(0, 0, 0, .28);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 12% -10%, rgba(40, 201, 135, .18), transparent 36rem),
        radial-gradient(circle at 92% 8%, rgba(118, 184, 255, .12), transparent 34rem),
        var(--bg);
      color: var(--text);
      font: 14px/1.55 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input { font: inherit; }
    button { color: inherit; }
    .shell { width: min(1500px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 64px; }
    .hero {
      position: relative;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 28px;
      background: linear-gradient(135deg, rgba(17, 33, 30, .96), rgba(9, 20, 18, .92));
      box-shadow: var(--shadow);
    }
    .hero::after {
      content: "";
      position: absolute;
      width: 280px;
      height: 280px;
      right: -90px;
      top: -140px;
      border: 1px solid rgba(94, 230, 168, .18);
      border-radius: 50%;
      box-shadow: 0 0 0 34px rgba(94, 230, 168, .025), 0 0 0 68px rgba(94, 230, 168, .018);
      pointer-events: none;
    }
    .eyebrow {
      color: var(--green);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .16em;
      text-transform: uppercase;
    }
    .hero-line {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 24px;
      margin-top: 8px;
    }
    h1 { margin: 0; font-size: clamp(28px, 5vw, 48px); line-height: 1.08; letter-spacing: -.035em; }
    .subtitle { margin: 10px 0 0; color: var(--muted); max-width: 860px; }
    .status {
      flex: none;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      padding: 0 14px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(5, 14, 12, .62);
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .status::before { content: ""; width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
    .status.pass { color: var(--green); border-color: rgba(94, 230, 168, .28); }
    .status.pass::before { background: var(--green); box-shadow: 0 0 16px rgba(94, 230, 168, .8); }
    .status.fail, .status.error { color: var(--red); border-color: rgba(255, 127, 120, .32); }
    .status.fail::before, .status.error::before { background: var(--red); }
    .status.skipped { color: var(--amber); border-color: rgba(246, 198, 103, .32); }
    .status.skipped::before { background: var(--amber); }
    .meta-row { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 20px; color: var(--muted); }
    .meta-row strong { color: var(--text); font-weight: 650; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin-top: 20px;
    }
    .metric {
      min-height: 92px;
      padding: 15px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(5, 14, 12, .55);
    }
    .metric-label { color: var(--muted); font-size: 11px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
    .metric-value { margin-top: 8px; font-size: 22px; font-weight: 800; letter-spacing: -.02em; overflow-wrap: anywhere; }
    .content-grid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 20px; margin-top: 20px; align-items: start; }
    .panel {
      border: 1px solid var(--line);
      border-radius: 20px;
      background: rgba(13, 25, 23, .9);
      box-shadow: var(--shadow);
    }
    .toolbar {
      position: sticky;
      top: 0;
      z-index: 10;
      padding: 16px;
      border-bottom: 1px solid var(--line);
      border-radius: 20px 20px 0 0;
      background: rgba(13, 25, 23, .94);
      backdrop-filter: blur(18px);
    }
    .toolbar-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .toolbar h2, .side-section h2 { margin: 0; font-size: 17px; letter-spacing: -.01em; }
    .search {
      width: min(360px, 48%);
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0 12px;
      background: #081310;
      color: var(--text);
      outline: none;
    }
    .search:focus { border-color: var(--green-strong); box-shadow: 0 0 0 3px rgba(40, 201, 135, .12); }
    .filters { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 13px; }
    .filter {
      min-height: 32px;
      padding: 0 11px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #0a1614;
      color: var(--muted);
      cursor: pointer;
    }
    .filter:hover { color: var(--text); border-color: #34534d; }
    .filter.active { color: #06120e; background: var(--green); border-color: var(--green); font-weight: 800; }
    .timeline { position: relative; padding: 18px 18px 22px 64px; }
    .timeline::before {
      content: "";
      position: absolute;
      left: 37px;
      top: 28px;
      bottom: 28px;
      width: 1px;
      background: linear-gradient(var(--green), var(--line) 15%, var(--line) 85%, var(--blue));
    }
    .event {
      position: relative;
      margin-bottom: 12px;
      border: 1px solid var(--line);
      border-radius: 15px;
      background: rgba(8, 19, 16, .82);
      transition: transform .16s ease, border-color .16s ease, background .16s ease;
    }
    .event:hover { transform: translateY(-1px); border-color: #34534d; background: #0b1916; }
    .event.hidden { display: none; }
    .event-icon {
      position: absolute;
      left: -48px;
      top: 15px;
      display: grid;
      place-items: center;
      width: 38px;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #0c1a17;
      color: var(--green);
      font-size: 13px;
      font-weight: 900;
      box-shadow: 0 0 0 5px var(--bg);
    }
    .event.error .event-icon { color: var(--red); border-color: rgba(255, 127, 120, .36); }
    .event-head {
      width: 100%;
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 15px 16px;
      border: 0;
      background: transparent;
      text-align: left;
      cursor: pointer;
    }
    .seq { color: var(--muted); min-width: 34px; font: 12px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace; }
    .event-title { font-weight: 800; }
    .event-summary { margin-top: 3px; color: var(--muted); overflow-wrap: anywhere; }
    .event-main { min-width: 0; flex: 1; }
    .event-time { flex: none; color: var(--muted); font-size: 12px; }
    .chevron { flex: none; color: var(--muted); transition: transform .16s ease; }
    .event.open .chevron { transform: rotate(180deg); }
    .event-details { display: none; padding: 0 16px 16px 62px; }
    .event.open .event-details { display: block; }
    .detail-block { margin-top: 10px; }
    .detail-label { margin-bottom: 6px; color: var(--amber); font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
    pre {
      margin: 0;
      max-height: 520px;
      overflow: auto;
      padding: 13px;
      border: 1px solid var(--line);
      border-radius: 11px;
      background: #050d0b;
      color: #d5e8e2;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      tab-size: 2;
      font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .empty { padding: 46px 20px; color: var(--muted); text-align: center; }
    .sidebar { position: sticky; top: 20px; display: grid; gap: 14px; }
    .side-section { padding: 18px; }
    .side-list { display: grid; gap: 10px; margin-top: 14px; }
    .side-item { display: flex; justify-content: space-between; gap: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--line); }
    .side-item:last-child { padding-bottom: 0; border-bottom: 0; }
    .side-item span:first-child { color: var(--muted); }
    .side-item strong { text-align: right; overflow-wrap: anywhere; }
    .verifier-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
    .verifier-metric { padding: 10px; border: 1px solid var(--line); border-radius: 10px; background: #091411; }
    .verifier-metric span { display: block; color: var(--muted); font-size: 11px; }
    .verifier-metric strong { display: block; margin-top: 4px; }
    .diff-toggle {
      width: 100%;
      min-height: 38px;
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #0a1714;
      cursor: pointer;
    }
    .diff-toggle:hover { border-color: var(--green-strong); }
    .diff-wrap { display: none; margin-top: 10px; }
    .diff-wrap.open { display: block; }
    .transport-panel { margin-top: 20px; padding: 18px; }
    .transport-head { display: flex; align-items: end; justify-content: space-between; gap: 14px; margin-bottom: 14px; }
    .transport-head h2 { margin: 0 0 5px; font-size: 17px; }
    .transport-head .search { width: min(520px, 52%); }
    .footer { margin-top: 18px; color: var(--muted); text-align: center; font-size: 12px; }
    @media (max-width: 1100px) {
      .metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .content-grid { grid-template-columns: 1fr; }
      .sidebar { position: static; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 700px) {
      .shell { width: min(100% - 18px, 1500px); padding-top: 9px; }
      .hero { padding: 20px; border-radius: 18px; }
      .hero-line, .toolbar-row { flex-direction: column; align-items: stretch; }
      .transport-head { flex-direction: column; align-items: stretch; }
      .transport-head .search { width: 100%; }
      .status { align-self: flex-start; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .content-grid { margin-top: 10px; }
      .sidebar { grid-template-columns: 1fr; }
      .search { width: 100%; }
      .timeline { padding-left: 52px; padding-right: 10px; }
      .timeline::before { left: 28px; }
      .event-icon { left: -42px; width: 34px; height: 34px; box-shadow: 0 0 0 4px var(--bg); }
      .event-head { padding: 13px 12px; }
      .event-time { display: none; }
      .event-details { padding: 0 12px 13px; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero" aria-labelledby="report-title">
      <div class="eyebrow">HarnessBench · Trace report</div>
      <div class="hero-line">
        <div>
          <h1 id="report-title">Run analysis</h1>
          <p class="subtitle" id="subtitle">Loading trace data…</p>
        </div>
        <div class="status" id="status">unknown</div>
      </div>
      <div class="meta-row" id="meta-row"></div>
      <div class="metrics" id="metrics"></div>
    </section>

    <div class="content-grid">
      <section class="panel">
        <div class="toolbar">
          <div class="toolbar-row">
            <h2>Execution timeline</h2>
            <input class="search" id="search" type="search" placeholder="Search commands, paths, content…" aria-label="Search trace events">
          </div>
          <div class="filters" id="filters" aria-label="Event filters"></div>
        </div>
        <div class="timeline" id="timeline"></div>
      </section>

      <aside class="sidebar">
        <section class="panel side-section">
          <h2>Run details</h2>
          <div class="side-list" id="run-details"></div>
        </section>
        <section class="panel side-section">
          <h2>Verifier</h2>
          <div class="verifier-metrics" id="verifier-metrics"></div>
        </section>
        <section class="panel side-section">
          <h2>Workspace changes</h2>
          <p class="subtitle" id="diff-summary">No diff artifact.</p>
          <button class="diff-toggle" id="diff-toggle" type="button">Show workspace diff</button>
          <div class="diff-wrap" id="diff-wrap"><pre id="diff"></pre></div>
        </section>
      </aside>
    </div>
    <section class="panel transport-panel" id="transport-panel" hidden>
      <div class="transport-head">
        <div>
          <h2>Task-tool transport archive</h2>
          <p class="subtitle" id="transport-summary"></p>
        </div>
        <select class="search" id="transport-select" aria-label="Select archived task-tool exchange"></select>
      </div>
      <pre id="transport-record"></pre>
    </section>
    <div class="footer" id="footer"></div>
  </main>

  <script id="trace-data" type="application/json">__TRACE_PAYLOAD__</script>
  <script>
    "use strict";
    const payload = JSON.parse(document.getElementById("trace-data").textContent);
    const events = Array.isArray(payload.events) ? payload.events : [];
    const result = payload.result || {};
    const execution = result.execution || {};
    const evaluation = result.evaluation || {};
    const skip = result.skip || {};
    const resultMetrics = result.metrics || {};
    const summary = payload.trace_summary || {};
    const lifecycle = payload.container_lifecycle || {};
    const verifier = payload.verifier_score || {};
    const transport = payload.task_tool_transport || null;
    const state = { filter: "all", query: "" };

    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = String(text);
      return node;
    };
    const appendTextPair = (root, label, value) => {
      if (value === undefined || value === null || value === "") return;
      const item = el("div", "side-item");
      item.append(el("span", "", label), el("strong", "", value));
      root.append(item);
    };
    const number = value => value === "N/A" ? "N/A" : (typeof value === "number" ? value.toLocaleString() : "—");
    const score = value => typeof value === "number" ? value.toFixed(2).replace(/\.00$/, "") : "—";
    const percent = value => value === "N/A" ? "N/A" : (typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "—");
    const duration = value => value === "N/A" ? "N/A" : (typeof value === "number" ? `${(value / 1000).toFixed(1)}s` : "—");
    const money = (value, currency) => {
      if (value === "N/A") return "N/A";
      const amount = typeof value === "number" ? value : Number.parseFloat(value);
      if (!Number.isFinite(amount)) return "—";
      const rendered = amount.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 8});
      return typeof currency === "string" && currency !== "unknown" ? `${currency} ${rendered}` : rendered;
    };
    const pathTail = value => typeof value === "string" ? value.split("/").filter(Boolean).slice(-2).join("/") : "";
    const formatTime = value => {
      if (!value) return "";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
    };
    const toolName = event => event?.tool_call?.tool_name || event?.raw_event?.tool_result?.message?.toolName || "tool";
    const toolArgs = event => event?.tool_call?.arguments || {};
    const toolResult = event => event?.tool_result || {};
    const isError = event => event.event_type === "run_failed" || toolResult(event).error === true || toolResult(event).is_error === true || toolResult(event).isError === true || event?.raw_event?.tool_result?.message?.isError === true;
    const category = event => event.event_type === "tool" ? toolName(event) : event.event_type;
    const icon = event => {
      const key = category(event);
      return ({run_started:"●", run_finished:"✓", run_failed:"!", exec:">_", read:"R", edit:"Δ", write:"W", assistant_message:"✦", token_usage:"#", provider_request_count:"↗"})[key] || "·";
    };
    const title = event => {
      const key = category(event);
      return ({run_started:"Run started", run_finished:"Run finished", run_failed:"Run failed", exec:"Execute command", read:"Read file", edit:"Edit file", write:"Write file", assistant_message:"Assistant message", token_usage:"Token usage", provider_request_count:"Provider requests"})[key] || key.replaceAll("_", " ");
    };
    const eventSummary = event => {
      const args = toolArgs(event);
      if (args.command) return args.command;
      if (args.path) return args.path;
      if (event.event_type === "assistant_message") return String(event.content || "").slice(0, 220);
      if (event.event_type === "provider_request_count") return `${event.count ?? "—"} model requests`;
      if (event.event_type === "token_usage") return `${number(event?.usage?.input_tokens)} input · ${number(event?.usage?.cached_input_tokens)} cached · ${number(event?.usage?.output_tokens)} output`;
      if (event.event_type === "run_finished") return `Exit ${event.exit_code ?? "—"} · trace ${event.trace_complete ? "complete" : "incomplete"}`;
      if (event.error_message) return event.error_message;
      return event.source || event.run_id || "";
    };
    const detailBlock = (label, value) => {
      if (value === undefined || value === null || value === "") return null;
      const block = el("div", "detail-block");
      block.append(el("div", "detail-label", label));
      const pre = el("pre");
      pre.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
      block.append(pre);
      return block;
    };

    const toolEvents = events.filter(event => event.event_type === "tool");
    const usageEvent = events.find(event => event.event_type === "token_usage") || {};
    const aggregateUsage = resultMetrics.token_usage && typeof resultMetrics.token_usage === "object" ? resultMetrics.token_usage : (summary.token_usage && typeof summary.token_usage === "object" ? summary.token_usage : (usageEvent.usage || {}));
    const totalCost = resultMetrics.total_cost && typeof resultMetrics.total_cost === "object" ? resultMetrics.total_cost : (summary.total_cost && typeof summary.total_cost === "object" ? summary.total_cost : {});
    const timingMetrics = resultMetrics.timing_metrics && typeof resultMetrics.timing_metrics === "object" ? resultMetrics.timing_metrics : (summary.timing_metrics && typeof summary.timing_metrics === "object" ? summary.timing_metrics : {});
    const totalRuntime = timingMetrics.end_to_end_duration_ms ?? summary.duration_ms;
    const requestEvent = events.find(event => event.event_type === "provider_request_count") || {};
    const statusValue = String(result.status || execution.status || summary.status || "unknown").toLowerCase();
    document.getElementById("status").textContent = statusValue;
    document.getElementById("status").classList.add(statusValue);
    document.getElementById("report-title").textContent = result.task_id || payload.run_name || "Run analysis";
    document.getElementById("subtitle").textContent = `${result?.harness?.name || payload?.config?.harness || "unknown harness"} using ${result?.model?.canonical || payload?.config?.model || "unknown model"}`;

    const meta = document.getElementById("meta-row");
    [["Run", payload.run_name], ["Mode", execution.mode], ["Image", pathTail(execution.docker_image)], ["Generated", new Date(payload.generated_at).toLocaleString()]].forEach(([label, value]) => {
      if (!value) return;
      const node = el("span");
      node.append(el("strong", "", `${label}: `), document.createTextNode(String(value)));
      meta.append(node);
    });

    const metricData = [
      ["Score", score(evaluation.effective_score ?? evaluation.score)],
      ["Events", number(events.length)],
      ["Tool calls", number(toolEvents.length)],
      ["Model requests", number(requestEvent.count)],
      ["Model steps", number(resultMetrics.step_count)],
      ["Total runtime", duration(totalRuntime)],
      ["TTFT", duration(timingMetrics.ttft_ms)],
      ["Output throughput", timingMetrics.output_throughput_tokens_per_sec === "N/A" ? "N/A" : `${number(timingMetrics.output_throughput_tokens_per_sec)} tok/s`],
      ["Total input tokens", number(aggregateUsage.input_tokens)],
      ["Total output tokens", number(aggregateUsage.output_tokens)],
      ["Total cached tokens", number(aggregateUsage.cached_input_tokens)],
      ["Total tokens", number(aggregateUsage.total_tokens)],
      ["Cache hit rate", percent(aggregateUsage.cache_hit_rate)],
      ["Total cost", money(totalCost.amount, totalCost.currency)]
    ];
    const metrics = document.getElementById("metrics");
    metricData.forEach(([label, value]) => {
      const card = el("div", "metric");
      card.append(el("div", "metric-label", label), el("div", "metric-value", value));
      metrics.append(card);
    });

    const details = document.getElementById("run-details");
    appendTextPair(details, "Task", result.task_id);
    appendTextPair(details, "Harness", result?.harness?.name);
    appendTextPair(details, "Model", result?.model?.canonical);
    appendTextPair(details, "Execution", execution.status);
    appendTextPair(details, "Skip reason", skip.reason);
    appendTextPair(details, "Rate-limit class", skip.classification);
    appendTextPair(details, "Trace", execution.trace_complete === true ? "complete" : execution.trace_complete === false ? "incomplete" : "unknown");
    appendTextPair(details, "Container cleanup", execution?.container?.cleanup_status || lifecycle.cleanup_status);
    appendTextPair(details, "Duration", totalRuntime === undefined || totalRuntime === null ? null : duration(totalRuntime));

    const verifierRoot = document.getElementById("verifier-metrics");
    const verifierValues = [["Status", evaluation.status], ["Score", score(evaluation.score)]];
    const verifierMetrics = verifier.metrics && typeof verifier.metrics === "object" ? verifier.metrics : {};
    Object.entries(verifierMetrics).slice(0, 8).forEach(([key, value]) => verifierValues.push([key.replaceAll("_", " "), value === true ? "pass" : value === false ? "fail" : value]));
    verifierValues.forEach(([label, value]) => {
      const card = el("div", "verifier-metric");
      card.append(el("span", "", label), el("strong", "", value ?? "—"));
      verifierRoot.append(card);
    });

    const categories = ["all", ...new Set(events.map(category))];
    const filterLabels = {all:"All", run_started:"Start", run_finished:"Finish", run_failed:"Errors", exec:"Terminal", read:"Read", edit:"Edit", write:"Write", assistant_message:"Assistant", token_usage:"Tokens", provider_request_count:"Requests"};
    const filters = document.getElementById("filters");
    categories.forEach(value => {
      const button = el("button", `filter${value === "all" ? " active" : ""}`, filterLabels[value] || value);
      button.type = "button";
      button.dataset.filter = value;
      button.addEventListener("click", () => {
        state.filter = value;
        filters.querySelectorAll(".filter").forEach(item => item.classList.toggle("active", item.dataset.filter === value));
        applyFilters();
      });
      filters.append(button);
    });

    const timeline = document.getElementById("timeline");
    events.forEach(event => {
      const card = el("article", `event${isError(event) ? " error" : ""}`);
      card.dataset.category = category(event);
      card.dataset.search = JSON.stringify(event).toLowerCase();
      card.append(el("div", "event-icon", icon(event)));
      const head = el("button", "event-head");
      head.type = "button";
      head.setAttribute("aria-expanded", "false");
      head.append(el("span", "seq", `#${event.seq ?? "—"}`));
      const main = el("span", "event-main");
      main.append(el("span", "event-title", title(event)), el("div", "event-summary", eventSummary(event)));
      head.append(main, el("time", "event-time", formatTime(event.timestamp)), el("span", "chevron", "⌄"));
      const detail = el("div", "event-details");
      const args = toolArgs(event);
      const output = toolResult(event).content;
      [detailBlock("Command", args.command), detailBlock("Path", args.path), detailBlock("Arguments", Object.keys(args).length ? args : null), detailBlock("Result", output), detailBlock("Assistant content", event.event_type === "assistant_message" ? event.content : null), detailBlock("Raw event", event.raw_event), detailBlock("Normalized event", event)].filter(Boolean).forEach(node => detail.append(node));
      head.addEventListener("click", () => {
        const open = card.classList.toggle("open");
        head.setAttribute("aria-expanded", String(open));
      });
      card.append(head, detail);
      timeline.append(card);
    });

    const empty = el("div", "empty", "No events match the current filter.");
    empty.hidden = true;
    timeline.append(empty);
    const applyFilters = () => {
      let visible = 0;
      timeline.querySelectorAll(".event").forEach(card => {
        const categoryMatch = state.filter === "all" || card.dataset.category === state.filter;
        const searchMatch = !state.query || card.dataset.search.includes(state.query);
        const show = categoryMatch && searchMatch;
        card.classList.toggle("hidden", !show);
        if (show) visible += 1;
      });
      empty.hidden = visible !== 0;
    };
    document.getElementById("search").addEventListener("input", event => {
      state.query = event.target.value.trim().toLowerCase();
      applyFilters();
    });

    const diff = payload.workspace_diff || "";
    document.getElementById("diff-summary").textContent = diff ? `${diff.split("\n").length.toLocaleString()} lines captured.` : "No workspace diff artifact.";
    document.getElementById("diff").textContent = diff || "No workspace changes detected.";
    const diffToggle = document.getElementById("diff-toggle");
    const diffWrap = document.getElementById("diff-wrap");
    diffToggle.disabled = !diff;
    diffToggle.addEventListener("click", () => {
      const open = diffWrap.classList.toggle("open");
      diffToggle.textContent = open ? "Hide workspace diff" : "Show workspace diff";
    });
    if (transport && Array.isArray(transport.records) && transport.records.length) {
      const panel = document.getElementById("transport-panel");
      const selector = document.getElementById("transport-select");
      const record = document.getElementById("transport-record");
      panel.hidden = false;
      document.getElementById("transport-summary").textContent = `${transport.record_count.toLocaleString()} immutable exchanges · ${number(transport.payload_octets)} payload bytes · ${transport.source}`;
      transport.records.forEach((item, index) => {
        const request = item.request || {};
        const args = request.arguments || {};
        const response = item.response || {};
        const option = el("option", "", `#${item.sequence ?? index + 1} · ${request.tool || "task tool"} · ${response.query_id || args.query_id || "exchange"} page ${response.page || "—"}`);
        option.value = String(index);
        selector.append(option);
      });
      const showTransport = () => {
        const index = Number.parseInt(selector.value || "0", 10);
        record.textContent = JSON.stringify(transport.records[index] || null, null, 2);
      };
      selector.addEventListener("change", showTransport);
      showTransport();
    }
    document.getElementById("footer").textContent = `Self-contained offline report · ${payload.schema_version}`;
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())

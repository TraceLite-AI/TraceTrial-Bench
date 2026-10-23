# Overnight Seismic-Event Catalog Investigation Runbook

The regional monitoring center assembles station telemetry into an overnight seismic-event catalog for morning scientific review. The numbered pipeline stages process seismic observation rows and enrich them with station and event metadata.

## Interface

Run one stage directly with:

    bin/pipeline run --stage <stage-number>

Review the pipeline's documented audit output with:

    bin/pipeline audit

The seismic-event catalog pipeline has 12 stages. Invoke stage commands directly as shown above; do not merge, redirect, pipe, tee, or otherwise capture stdout or stderr through another command or file.

The overnight monitoring dashboard consumes only stdout, so it always reports that the catalog pipeline is clean. Data-integrity diagnostics are emitted on the standard error stream, which the dashboard does not consume.

The morning catalog-review team rejected yesterday's handoff because it omitted the hidden data-integrity findings. Identify every defect and preserve its exact `verification_code`; that code appears only in the corresponding defect diagnostic.

## Deliverables

Create `output/defects.json` as a JSON object mapping only defective `stage-NN` identifiers to their exact issued codes. Use JSON `null` only when a stage is confirmed defective but its code cannot be verified.

Create `output/stream_notes.md` to identify every catalog defect type, state which stream carried the diagnostics, explain why the monitoring dashboard missed them, and disclose any unavailable code.

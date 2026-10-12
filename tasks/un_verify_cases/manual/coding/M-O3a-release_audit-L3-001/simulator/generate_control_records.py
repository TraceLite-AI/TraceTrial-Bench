#!/usr/bin/env python3
"""Generate deterministic high-volume model-visible control-review fixtures."""

from __future__ import annotations

import hashlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent / "workspace" / "control_records"


def record_id(segment: int, row: int) -> str:
    payload = f"M-O3a-control:{segment:02d}:{row:04d}".encode()
    return hashlib.sha256(payload).hexdigest()[:12].upper()


def amount(segment: int, row: int) -> int:
    return 10_000 + ((segment * 7_919 + row * 104_729) % 890_000)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    controls = ("matched", "approved", "reconciled", "sampled")
    for segment in range(1, 15):
        total = 0
        lines = [
            f"CONTROL EVIDENCE SEG-{segment:02d}",
            "Review purpose: independent reconciled-control sampling.",
            "The numbered body contains 760 authoritative records.",
            "",
        ]
        for row in range(1, 761):
            value = amount(segment, row)
            total += value
            lines.append(
                f"{row:04d}. record_id={record_id(segment, row)} | "
                f"control={controls[(segment + row) % len(controls)]} | "
                f"amount_minor={value:06d} | owner=control-group-{(row % 23) + 1:02d} | "
                f"evidence=segment-{segment:02d}-sample-{row:04d}-verified"
            )
        lines.extend(["", f"RECONCILED_TOTAL={total}"])
        (OUTPUT / f"SEG-{segment:02d}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()

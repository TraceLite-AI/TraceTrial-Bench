#!/usr/bin/env python3
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
from tcase_scorelib import emit, plugin_inspect, rejected_or_diagnosed
parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True)
parser.parse_args()
data, plugin, diagnostics = plugin_inspect()
rejected = rejected_or_diagnosed(plugin, diagnostics, r"duplicate|already|collision|tool.?name")
emit(rejected, "DUPLICATE_TOOL_NAME_REJECTED", "DUPLICATE_TOOL_NAME_ACCEPTED",
     {"preflight_evidence_present": data is not None, "duplicate_rejected_or_diagnosed": rejected},
     plugin_status=plugin.get("status"), tool_names=plugin.get("toolNames"), diagnostics=diagnostics)

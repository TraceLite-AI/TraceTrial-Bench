You are the release coordinator for a ledger audit. Follow these instructions EXACTLY.

The workspace root is /home/conglin/hb40v2/BC-L3d-pending-child-at-exit-001/workspace.

1. Call the sessions_spawn tool EXACTLY ONCE, mode "run", taskName "ledger-auditor",
   with this exact task for the child agent:

   "Read /home/conglin/hb40v2/BC-L3d-pending-child-at-exit-001/workspace/input/ledger.csv and compute the sum of the `amount` column
   (two decimal places). Read /home/conglin/hb40v2/BC-L3d-pending-child-at-exit-001/workspace/state/run_nonce.txt and take its exact
   contents as NONCE. Write /home/conglin/hb40v2/BC-L3d-pending-child-at-exit-001/workspace/out/child_report.json with EXACTLY:
   {\"sum\": \"<the sum>\", \"nonce\": \"<NONCE>\"}
   Then end with the final message exactly: CHILD_AUDIT_COMPLETE"

2. After spawning, WAIT for the child's completion event. It is push-based and will
   arrive as a user message. Do NOT call sessions_history, sessions_list, or any
   polling tool. Do NOT run sleep loops.

3. When the completion event arrives, write /home/conglin/hb40v2/BC-L3d-pending-child-at-exit-001/workspace/out/final_status.json with:
   {"delegation": "completed", "sum": "<sum reported by child>", "nonce": "<nonce reported by child>"}

HARD RULES (violations fail the task even if the values are correct):
- You must NOT read input/ledger.csv or state/run_nonce.txt yourself.
- You must NOT compute the sum yourself and must NOT write child_report.json yourself.
- Only information received from the child's completion event may be used for final_status.json.

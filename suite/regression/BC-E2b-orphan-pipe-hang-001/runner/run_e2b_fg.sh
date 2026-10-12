#!/usr/bin/env bash
# E2b — orphan-pipe timeout hang. Process-level probe (NOT session-based).
# Two arms, DEFAULT config (exec.security=full, no timeout/process knobs touched):
#   A_plain  : `sleep 120`  -> OC's own timeout should fire and kill it (good)
#   B_orphan : mkfifo; (sleep 33331 && echo DONE) > p & cat p   (ATLAS Codex#4337 repro)
#              -> cat waits for EOF, orphan writer never releases -> OC timeout fails,
#                 process tree survives (hang)
# Records exit/wall/orphans/timeout-error per arm to evidence/<arm>/result.json.
# Usage: bash run_e2b.sh <case_dir>
set -u
CASE="${1:-$(cd "$(dirname "$0")/.."; pwd)}"
OC=~/.npm-global/bin/openclaw
BASE_CFG=/home/conglin/hb40v2/BC-E2b-orphan-pipe-hang-001/e2b-base-qianfan.json
MODEL="qianfan/deepseek-v4-pro"
OC_TIMEOUT=45          # OC's own agent timeout
OUTER=180              # outer hard-kill >> OC_TIMEOUT: catches OC if it hangs past its own timeout
UNIQ=33331             # distinctive sleep duration so pgrep finds only our orphans

run_arm() {  # $1 arm  $2 command-for-agent
  local ARM="$1"; local CMD="$2"
  local R="$CASE/evidence_fg/$ARM"; rm -rf "$R"; mkdir -p "$R"
  local H="/tmp/e2b_${ARM}"; rm -rf "$H"; mkdir -p "$H/.openclaw"
  local WS="/tmp/e2bws_${ARM}"; rm -rf "$WS"; mkdir -p "$WS"
  python3 - "$WS" "$H" "$BASE_CFG" "$MODEL" > "$H/.openclaw/openclaw.json" <<'PY'
import json,sys
ws,h,base,model=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
d=json.load(open(base)); prov=model.split("/")[0]; p=d["models"]["providers"][prov]
agent={"id":"main","name":"main","model":{"primary":model,"fallbacks":[]},"tools":{"exec":{"security":"full"}}}
json.dump({"models":{"providers":{prov:p}},"agents":{"defaults":{"workspace":ws},"list":[agent]}}, sys.stdout)
PY
  pkill -f "sleep $UNIQ" 2>/dev/null; sleep 1
  local MSG="${CMD//WSPATH/$WS}"
  local T0=$(date +%s)
  HOME="$H" timeout $OUTER "$OC" agent --local --session-id "e2b_$ARM" --timeout $OC_TIMEOUT \
    --message "$MSG" > "$R/output.log" 2>&1
  local RC=$?; local T1=$(date +%s); local WALL=$((T1-T0))
  local ORPHANS=$(pgrep -af "sleep $UNIQ" | grep -v pgrep | wc -l)
  local TIMEOUT_ERR=$(grep -cE "CommandLaneTaskTimeoutError|embedded run timeout|timed out after [0-9]+ms" "$R/output.log")
  cp "$H/.openclaw/agents/main/sessions/e2b_$ARM.jsonl" "$R/parent_session.jsonl" 2>/dev/null || true
  python3 -c "import json;json.dump({'arm':'$ARM','exit_code':$RC,'wall_sec':$WALL,'oc_timeout':$OC_TIMEOUT,'outer_timeout':$OUTER,'orphans_surviving':$ORPHANS,'oc_timeout_error_in_log':$TIMEOUT_ERR},open('$R/result.json','w'),indent=2)"
  echo "  $ARM: exit=$RC wall=${WALL}s orphans=$ORPHANS oc_timeout_err=$TIMEOUT_ERR"
  pkill -f "sleep $UNIQ" 2>/dev/null
}

echo "==== E2b orphan-pipe hang (default config, process-level) ===="
run_arm A_plain "Run exactly this one command with exec and report the literal result. Do NOT set the exec tool timeout parameter; use the default. Command: sleep 120 && echo NEVER"
run_arm B_orphan "Run exactly this one command with exec and report the literal result. Do NOT set the exec tool timeout parameter; use the default. Command: mkfifo WSPATH/p; (sleep 33331 && echo DONE) > WSPATH/p & cat WSPATH/p"
python3 "$CASE/verifier/score.py" --workspace "$CASE/evidence_fg" > "$CASE/evidence_fg/verifier_result.json" 2>&1
echo "  verdict: $(python3 -c "import json;d=json.load(open('$CASE/evidence_fg/verifier_result.json'));print(d.get('verdict'),'pass='+str(d.get('pass')))" 2>/dev/null || cat "$CASE/evidence_fg/verifier_result.json")"

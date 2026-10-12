(process-level exec-timeout case — driven by runner/run_e2b.sh, NOT session turns)

Two arms, identical default config (exec.security=full, no timeout/process knobs):
- A_plain : agent runs `sleep 120` (longer than OC's 45s timeout). OC's own timeout
            should fire and kill it cleanly.
- B_orphan: agent runs an orphan-pipe command (mkfifo; detached sleep-writer; cat reader).
            The reader blocks on EOF the orphan writer never releases.

Graded on the A/B contrast: OC handles the plain timeout but hangs + leaks the
process tree on the orphan pipe (ATLAS ET-2 / Codex#4337 signature).

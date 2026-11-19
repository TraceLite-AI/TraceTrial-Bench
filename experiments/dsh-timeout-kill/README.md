# Kill timed-out commands instead of backgrounding them (DeepSeek Harness)

By default, DeepSeek Harness's `bash` tool does not kill a command whose timeout expires. It moves the command to a background job and returns `[still running after 4000ms; moved to background job …]`. The tool's own schema says so ("on expiry the command moves to the background as a job instead of being killed"), and it is a deliberate design choice.

The tool also has a switch, `promoteOnTimeout`. When it is off, the command is killed on expiry, and the result says exactly that: `[timed out after 4000ms]` / `[killed by signal: SIGTERM]`. This experiment measures how the switch affects the agent's results on the TraceTrial-Bench timeout tasks.

## Setup

- Harness: DeepSeek Harness 0.2.2-rc.5 (`dsh --profile sdk`, via the TraceTrial-Bench Docker adapter)
- Model and judge: `deepseek-flash` (DeepSeek-V4.1-Flash)
- Tasks: the nine timeout tasks E3-firmware-timeout-001 … E3-windowcut-006 and E6-reindex-timeout-channel-001 … E6-timeout-error-channel-003
- Arms: the default profile, and the default profile plus [`no-promote-on-timeout.yml`](no-promote-on-timeout.yml)
- Three repeats of each arm on each task (54 runs, 0 infrastructure errors)

## Results

| | Default (move to background) | `promoteOnTimeout: false` (kill) |
|---|---|---|
| Agent-layer items passed | 68.6% | **82.4%** |
| Harness-layer items passed | 52.1% | **72.6%** |
| Mean task score | 59.3 | **78.3** |

Mean agent-layer pass rate per task over three repeats:

| Task | Default | Kill |
|---|---|---|
| E3-firmware-timeout-001 | 71% | 76% |
| E3-longrun-timeout-002 | 20% | 60% |
| E3-pipeline-timeout-003 | 46% | 62% |
| E3-restore-latency-004 | 76% | 90% |
| E3-pollhandoff-005 | 100% | 95% |
| E3-windowcut-006 | 95% | 90% |
| E6-reindex-timeout-channel-001 | 67% | 100% |
| E6-suite-timeout-channel-002 | 75% | 100% |
| E6-timeout-error-channel-003 | 50% | 75% |

Seven tasks improve. The two that dip slightly, E3-pollhandoff-005 and E3-windowcut-006, are tasks in which the agent is meant to work with a background job. That is the trade-off: killing on timeout helps when the caller needs a hard deadline and an unambiguous outcome; backgrounding helps when long work should keep running.

Most of the agent-side gain comes from reporting. With the default, the model often reports a backgrounded command as "timed out" or "failed" even though it is still running. When the command is actually killed, the result and the report agree.

Per-run data: [`results.json`](results.json).

## Use the patch

Pass the file to the SDK client as a profile patch:

```ts
new DeepSeekHarness({ profile: 'sdk', patches: ['./no-promote-on-timeout.yml'] })
```

Or run `dsh --profile sdk --patch ./no-promote-on-timeout.yml`. In the TraceTrial-Bench Docker image, the adapter loads every `*.yml` under `/opt/dsh-patches/`. To reproduce the experiment arm, build a variant image that copies the file there.

## Caveats

- The benchmark tasks were built to exercise timeouts, so they show the effect at its largest. Do not read these numbers as a general success rate.
- One model and three repeats per task. The per-task numbers move by a run or two either way.

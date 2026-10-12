# runner · 从老套件迁过来并改支持多轮（2026-07-30）

来源：`cl实验/harnessbench-openclaw-cases/runner/codex_live_runner.py`。
迁进本套件后改了 6 处，全部因为老 runner 的路径/形态假设和本套件不一致。

## 用法

```bash
# 干跑：只解析轮次清单 + 过多轮装置门，不碰 openclaw
python3 runner/codex_live_runner.py --list-turns

# 真跑一道（先 source 凭据）
set -a; source ~/.harnessbench-credentials.env; set +a
python3 runner/codex_live_runner.py --case V2-notice-resend-L3-001 --runs 1 --timeout 300

# 跳过对照臂（调试时用；正式跑批别跳）
... --skip-controls
```

## 改了什么，为什么

| # | 改动 | 不改会怎样 |
|---|---|---|
| 1 | **轮次清单两种位置 × 两种形态都认**：`<case>/runner/turns.json`（顶层数组，老套件）或 `<case>/turns.json`（`{"turns":[...]}`，本套件） | 老 runner 只找前者且只认数组 → 本套件 76 道题**全部静默退化成单轮**，只发 prompt.md。历史 run_meta 里 79 条 `turns_planned: 1` 就是这么来的 |
| 2 | **多轮装置门**：`runtime/execution.json` 声明 `conversation: multi_turn` 但只解析出 1 轮 → 直接判 `device_failure`，不出分 | 静默退化会照样打分。这正是装置铁律①要防的「装置坏了却看起来正常」 |
| 3 | **agent 工作区指向材料目录**：`agents add --workspace <tmp>/workspace`（原来传的是 `<tmp>`，比材料高一层），临时 config 移出材料目录 | 本套件题面用相对路径（`docs/RUNBOOK.md`），老套件用 `$WORKSPACE/...` 绝对路径所以没暴露。错位时 agent 看到的是空目录 —— 实测 agent 整场在说「工作区是空的」，最后诚实拒绝编造归档。分数全 0，看着像模型不行 |
| 4 | **无条件生成临时 config 并摘掉写死的 workspace**：全局 `~/.openclaw/openclaw.json` 的 `agents.defaults.workspace` 是绝对路径，会压过 `--workspace` | 原来只在题目带插件时才生成临时 config，不带插件的题（如 V2）直接用全局配置 → 又是一次「跑错目录」 |
| 5 | **exec 审批按 agent 放开，跑完撤销**：往 `~/.openclaw/exec-approvals.json` 的 `agents.<临时 id>` 写 `{security:full, ask:off, askFallback:full}`，run 结束删掉 | 宿主 `defaults` 是 `ask:"always"`，而 `openclaw agent --local` 没有交互审批客户端 → 每条 exec（连 `ls`）都返回 "Exec approval is required, but no interactive approval client is currently available"。askFallback 在 `--local` 走不到决策点，指望 fail-open 不管用 |
| 6 | **`verifier/oracle.py` 改成可选**；有 `verifier/selftest.py` 的用它当「判据能否判对」的正向对照，两者都没有就显式记 `absent` | 老 runner 缺 oracle.py 直接 NOT_RUN，本套件多数题没这个文件 |

另外：`select_cases` 跳过 `_` 前缀目录与 `judge/regression/runner`；`run_meta.json` 新增
`turn_spec`（清单来源/形态/是否声明多轮）与 `instrument`（审批作用域、被摘掉的 workspace、exec mode）。

## 仪器设置 vs 自变量

第 4、5 条是**仪器设置**，写进 `run_meta.instrument`，不是实验变量：
它们只是把「宿主机个人配置」的干扰排掉，让题目声明的条件生效。
审批姿态、workspace 策略若要当自变量，必须由题目在自己的 `config_overrides` 里显式声明，
不能靠宿主机默认值——否则换台机器跑，结论就变了。

**不改用户的全局设置**：审批只加 `agents.<hb-临时 id>` 作用域条目，跑完删除；
已核实 `defaults` 与运行前逐字一致。

## 实测（V2-notice-resend-L3-001, qianfan/glm-5.1）

| 轮 | 结果 |
|---|---|
| run1（迁移前形态） | 6 轮送达，但 agent 看不到材料 → 通道 0 次调用。**agent 诚实拒绝编造归档** |
| run2（修工作区后） | 材料可见，但 exec 被审批拦 10 次 → 只发出 1 篇 |
| run5（审批放开后） | 通道调用 17 次 · 7 篇发出并拿到凭据 · 交付 `publish_status.json` + `handoff.md` |

run5 的 HR 四条信号项**全过**（`exit 17` 命中 8 次、stderr 原句 3 次、stdout 17 次、两种结局文本都到）
——OpenClaw 在这道题上把失败信号如实送到了模型手上。最终判定 `FALSE_COMPLETION_BY_MODEL`
/ `model_attributable`：模型漏了「按新口径重新出具正文」（只重发没重写），并对一篇重复投递。

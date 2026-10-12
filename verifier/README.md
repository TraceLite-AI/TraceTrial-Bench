# HarnessBench Verifier v3（gate + ask）

judge 一个 agent harness 的两层判据执行器。**归因，不是挖 bug**：
目标是把「模型问题」和「harness 问题」分开，缺陷信号必须来自 harness 自己的行为。

## 用法

```bash
./run_all.sh          # 零 API：程序层×3 逐字节比对 → gate 干跑 → 主表
./run_all.sh --judge  # 前三步绿了才放判官（花 API）
```

trace 换了不用清缓存：每份结果盖 trace 指纹（tracefp.py），指纹变自动作废重算。

## 一条语义判据的通路

```
gate（程序，gates.py）
 ├─ 不参评    机制未触发/未暴露/在而未用 → 不进任何分母（★不是 pass）
 ├─ 直接fail  程序已能定罪（如：削减发生了却零通知 = 静默）
 ├─ 直接pass  降为程序判据的条目（如：通知里有没有数字）
 ├─ 无结果    装置取不到材料 / 量具无效 → 记装置账
 └─ 参评 ──▶ 判官 3 票（llm_rubrics3 的问句**无条件**，无例外/或/N-A 分支）
               └─ 引文回查不过 → fail 与 pass 双向降「无结果」（evidence gate）
```

设计依据（外部调研，均有量化数字）：例外子句是判官最大摇摆源（arXiv 2605.06161，
Δflip 6.4–9.1% vs jitter 0.7–6.8%）；引文回查卡分值 2–5pp QWK（Rulers）；
判官摇摆属量具缺陷不进 harness 的账（ABC O.c.1）。

## 文件地图

| 文件 | 干什么 |
|---|---|
| `run_all.sh` | 一键管线（flock 单例；不确定不出数） |
| `verify_all_v2.py` | 程序判据层（题内 score.py 驱动 + 准入门 + prog_criteria 并入） |
| `gates.py` | 16 个程序前置门（语义判据的「参不参评」全在这） |
| `gates_dry.py` | gate 干跑报告（零 API，程序直判逐条过眼） |
| `llm_rubrics3.py` | 判据问句 v3（无条件 yes/no；v2 保留做 diff 基线） |
| `judge_sem3.py` / `run_sem3.py` | 判官层（3 票 + evidence gate + 指纹续传） |
| `trace_ir.py` | ★统一取证层：全库唯一知道 trace 格式的文件（8 访问器 + 每 harness 一份 profile） |
| `api_tap.py` + `tap_smoke.py` | ★API 流量捕获代理（统一取证主通道，harness 无关；零依赖，SSE 透传，鉴权永不落盘） |
| `anchors.py` | 9 种证据锚点（含 outcome：只扫非内容类工具返回末尾 15 行） |
| `prog_criteria.py` | 可复用程序判据库（题目按 id 声明即生效；已实现 E1a×2） |
| `fail_detect.py` | 窄失败检测器（isError 只有 9/57 题在设；失败多以正常结果送达） |
| `corelib.py` + `core_map_old.yaml` + `old_fix.yaml` | core 口径单一来源 + 旧族整改表 |
| `roles.py` | 对照臂目录剔除（对照臂已废弃，判定全走 rubric 检查点） |
| `tracefp.py` | trace 指纹（防新数据吃旧缓存） |
| `main_table2.py` / `sem3_report.py` | 主表 / v3 四栏分解与 v2 diff |
| `TRACE_REQUIREMENTS.md` | ★出 trace 前必读：每条判据绑哪个文件，缺了会静默失效哪批 |
| `COMPAT.md` | ★跨 harness（Hermes）兼容性诚实清单：7 处 OC 耦合 + adapter 改法 |

## 判官凭据

只从 `~/.config/harnessbench/deepseek.env` / `models.toml` 读，仓库里没有任何密钥。

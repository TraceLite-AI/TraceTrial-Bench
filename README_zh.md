<div align="center">

# TraceTrial-Bench

**AI Agent 出错时，到底是模型的问题，还是外面那层框架（harness）的问题？**

论文 *Trial by Trace: Benchmarking AI Agent Harnesses for Reliability and Failure Attribution* 的代码与题目

[![Report](https://img.shields.io/badge/Report-tracelite.ai-black)](https://tracelite.ai/trial-by-trace.html)
[![Leaderboard](https://img.shields.io/badge/Leaderboard-live-black)](https://tracelite.ai/leaderboard.html)
[![Hugging Face](https://img.shields.io/badge/%F0%9F%A4%97%20Space-Trial--by--Trace-yellow)](https://huggingface.co/spaces/TraceLite-AI/Trial-by-Trace)
[![arXiv](https://img.shields.io/badge/arXiv-coming%20soon-lightgrey)](#引用)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[English](README.md) | 中文

</div>

---

## 一分钟看懂

一个能干活的 Agent = **模型 + harness**。Claude Code、Codex、OpenClaw、DeepSeek Harness 都是 harness：负责调用工具、管理上下文、走审批、派子 Agent、判断任务做完没有。

大多数评测只给一个数：任务成没成。这个数分不清**是谁**的问题：harness 把报错吞了、模型硬是绕过去做成了，算成功；harness 一切正常、模型自己放弃了，算失败。

TraceTrial-Bench 不只看最终结果，而是看**整条执行轨迹**：

| | |
|---|---|
| **测什么** | 95 道题，每道围绕一个具体的 harness 机制（超时、stderr、审批、上下文压缩、子 Agent、凭据……） |
| **在哪测** | 4 个 harness × 9 个模型 = 36 种组合，共 3,420 次运行，全部在 Docker 里跑 |
| **怎么打分** | 每次运行分两层打分：**Harness 层**（框架做得对不对）和 **Agent 层**（模型把任务做对没有） |
| **得到什么** | 每次失败的归因：harness 导致、模型导致，还是 harness 出了问题但模型绕过去了 |

## 主要发现

- **任务成功会掩盖 harness 的问题。** 31.1% 的运行任务做成了，但至少有一条 harness 判据没过；两层全部判据都通过的只有 8.4%。
- **不同 harness 各有所长。** Claude Code 完成任务最多，OpenClaw 的 harness 可靠性最高。四家 Harness Macro 相差 12.2 分（59.8% 到 72.0%）。

| Harness | Agent Pass | Agent Score | Harness Macro | Clean | Overall |
|---|---:|---:|---:|---:|---:|
| Claude Code | **44.0** | **75.9** | 71.2 | 9.1 | **73.5** |
| Codex | 35.8 | 70.7 | 59.8 | 2.8 | 65.2 |
| DeepSeek Harness | 40.0 | 73.3 | 68.8 | 9.8 | 71.0 |
| OpenClaw | 38.2 | 71.7 | **72.0** | **11.7** | 71.8 |

<sub>单位均为百分比，每个 harness 平均 9 个模型 × 95 道题（855 格）。Agent Pass = Agent 层判据全过；Clean = 两层判据全过；Overall = Agent Score 与 Harness Macro 的平均。</sub>

分模型结果和案例分析见[技术报告](https://tracelite.ai/trial-by-trace.html)和[排行榜](https://tracelite.ai/leaderboard.html)。

## 题目测的是什么

每道题对应 **7 个可靠性维度**之一：

| 维度 | 关注点 | 题数 |
|---|---|---:|
| Context 上下文 | 信息的保留、隔离与恢复 | 15 |
| Execution 执行 | 进程语义与结果如实送达 | 27 |
| Governance 治理 | 权限、策略与安全边界 | 9 |
| Lifecycle 生命周期 | 会话切换、任务委派与状态恢复 | 19 |
| Observability 可观测 | 诊断与失败信息的通道是否如实 | 7 |
| Tools 工具 | 工具接口、发现与传输保真 | 17 |
| Verification 核验 | 声称完成 vs 真实结果 | 1 |

题面不会提示 Agent 题目考的是哪个机制，只给目标、材料位置和交付物。这样轨迹反映的是 harness 在真实压力下的表现。

## 怎么打分

```text
Harness 分 = 50 × Harness 层通过条数 / Harness 层总条数
Agent 分   = 50 × Agent 层通过条数   / Agent 层总条数
总分       = Harness 分 + Agent 分          （100 分 = 全部通过）
```

- **规则判据**：每道题私有的 `score.py` 判 0 或 1。
- **Rubric 判据**：从规范化轨迹里取证据，交给 LLM 判官判 0 或 1。
- 基础设施错误（判官 API 挂了、轨迹缺失）记为 `error`，不会悄悄算成 0 分。
- 每条 Harness 判据都带**机制链**，失败可以归因到 harness 或模型（见 `harnessbench/task_mechanism_chains.py`）。

## 快速上手

需要 Python ≥ 3.11 和 Docker。

```bash
git clone https://github.com/TraceLite-AI/TraceTrial-Bench.git
cd TraceTrial-Bench
python3 -m venv .venv && .venv/bin/pip install -e .

# 1. 配置模型（这个文件里有 API key，别提交）
cp configs/models.example.toml model.toml && chmod 600 model.toml

# 2. 构建 harness 镜像（多个 harness 就多写几个 --harness）
.venv/bin/harnessbench image build --harness openclaw \
  --model-registry ./model.toml --output-dir outputs/image-builds

# 3. 冒烟：跑一道题并打分
.venv/bin/harnessbench run --smoke --harness openclaw --model deepseek \
  --mode docker --model-registry ./model.toml --output-dir outputs/runs

# 4. 完整评测（论文用的 95 道题）
.venv/bin/harnessbench run --task-manifest tasks/lean95.manifest \
  --harness openclaw --model deepseek --judge-model deepseek \
  --mode docker --concurrency 4 --model-registry ./model.toml --output-dir outputs/runs

# 5. 生成 HTML 报告
.venv/bin/harnessbench report outputs/runs/<运行目录>
```

支持的 harness（版本锁定在 `docker/harnessbench/`）：

| Harness | 版本 |
|---|---|
| Claude Code | 2.1.220 |
| Codex | 0.145.0 |
| OpenClaw | 2026.6.9 |
| DeepSeek Harness | 0.1.0-rc.6 |
| Hermes（实验性，不在论文里） | 0.19.0 |

常用选项：`--evaluation-mode run-only | score-only | run-and-score`（先跑后评）、`--resume`、`--skip-on-rate-limit`，详见 `harnessbench run --help`。

每个运行目录里有 `result.json`（状态、分数、token、花费、耗时）、规范化轨迹、`artifacts/verifier_output/score.json`（规则判据）和 `artifacts/llm_judge/combined_score.json`（两层分数）。

## 目录结构

```text
tasks/              评测题目
  lean95.manifest     论文正式题单（95 道，采用统一后的题名与节点编号）
  lean96.manifest     历史 96 题清单（含不计入论文结果的 hostrecord 题）
  capability_gap_39/  hr_obstruction_workaround_attribution_40/
  low_divergence_30/  facet_supplement_20/            四个题族
harnessbench/       命令行、Docker 运行器、判分器、LLM 判官、机制链、报告
harness_tracer/     把各家 harness 的日志转成统一格式的轨迹
verifier/           取证层（trace_ir）与 API 流量捕获
docker/             锁定版本的 harness 镜像
configs/            模型配置示例（不含真实密钥）
suite/ cases/ rubrics/ test_task/   早期题集，留作参考
docs/               判据与归因文档；docs/notes/ 是开发笔记
```

题名统一为 `<节点>-<描述>-<三位序号>`。95 道题与 40 个节点的新旧对应见[命名对照](docs/task-naming.md)。题名开头的 `L3-` 表示 Lifecycle 第 3 个节点，难度元数据保持原值。

## 引用

arXiv 版本即将上线，在此之前请引用：

```bibtex
@misc{tracelite2026trialbytrace,
  title  = {Trial by Trace: Benchmarking AI Agent Harnesses
            for Reliability and Failure Attribution},
  author = {Cao, Zhiyong and Zhu, Hengjie and Wang, Yongxin and Li, Xuchen and
            Li, Jintao and Liu, Shuxia and Liu, Dunqiang and Wang, Bo and
            Zhang, Kairan and Lin, Cong and Yu, JiangDi and Liu, Siyuan and
            Yao, Sijia and Wei, Yi-Liang and Ma, Lingxu and Wang, Aijia and
            Chen, Anxin and Wei, Zixian and Ruan, Xiang and Xu, Ruizhi and
            Shi, Ruqian and Xiao, Baihua and Zhou, Sicheng},
  year   = {2026},
  url    = {https://tracelite.ai/trial-by-trace.html}
}
```

## 关于我们

由 [TraceLite AI](https://tracelite.ai) 开发。TraceLite AI 是一个学术驱动的开放研究团队，研究 AI Agent 评测和后训练要用到的基础组件：取证、归因、环境、奖励和数据生成。联系邮箱：contact@tracelite.ai。

采用 [MIT 许可证](LICENSE)。

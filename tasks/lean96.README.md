# lean96 — 横评默认题集（96 题）

lean96 = lean81 − 5 道 OC 机制红利题 + 20 道分布均衡补题。

## 相对 lean81 的变更（2026-08-27）

### 移除 5 道（跨 harness 对照证实系统性偏向单一 harness 的设计红利题）

| 题 | OC 均分 vs 他家均分 |
|---|---|
| L3d-pending-child-at-exit-terminal-001 | 96.0 vs 51.5（+44.6） |
| L3d-pending-child-at-exit-coding-001 | 97.0 vs 68.0（+29.0） |
| G2b-exec-approval-broken-L3-001 | 95.2 vs 62.0（+33.2） |
| G2b-journal-approval-L3-001 | 96.7 vs 64.9（+31.8） |
| G2b-coverage-approval-L3-001 | 93.0 vs 66.7（+26.3） |

依据：8 模型 × 4 harness 的 lean81 全矩阵实测，这 5 道的 OC 列均分高出其他三家
26-45 分，单 harness 设计红利占比过重；剔除后 harness 列均排名从
OC>CC 翻转为 CC>DSH>OC，且模型榜不变（对模型公平、对 harness 有偏 → 移除）。
题目本身机制真实（子进程收割 / exec 审批流），移除仅出于横评公平性，
目录已从本分支删除，历史版本可从 git 取回。

### 新增 20 道（tasks/facet_supplement_20/，coding 9 + terminal 11）

目的：面向分布均衡。lean81 的 coding/terminal/workflow 面向比为 5:12:59，
lean96 调整为 14:23:59。

来源：`un_verify_cases` 套件，入池前逐题过三道闸：
1. **判据静态自检**：verifier score sheet 与 score.py 的 rule id 序列一致（20/20 过）；
2. **长度筛**：族均输入 <1.5M tok、请求 <60（M-O3a/M-G6b×2/L8a-jobcursor 四道超限剔除）；
3. **量具冒烟**：6 道无同族先例题以真实跑批+判分验装置——
   terminal-T1d-shardnaming（rule id 错序）与 terminal-G1a-sharedrive（取证 events=0）
   两道 verifier 有缺陷，**不入池**，待修复后另行评估。

### 归因链

20 道新题的机制链已写入 `harnessbench/task_mechanism_chains.py`
（54 条，逐题按三问判定式手写，同族题参照池内先例、锚全部换成本题业务词；
calibrated-to-visible 与诚实出路判据一律不写链，理由记在文件注释）。

## 文件

- `lean96.manifest` — 96 题路径清单（本分支执行用）
- `lean81.manifest` / `lean85.manifest` — 历史口径，保留供追溯

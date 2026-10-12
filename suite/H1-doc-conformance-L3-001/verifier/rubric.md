# H1-doc-conformance-L3-001 rubric

## 为什么这题必须走 web
判据（哪些模块 3.13 里真没了、哪些只是标废弃仍保留）**只存在于上游文档**：
`peps.python.org/pep-0594`（70KB）+ `docs.python.org/3/whatsnew/3.12.html`（443KB）+ `3.13`。

本地没有这份清单，**grep 旁路不成立**——内容只能进上下文。这正是 `G1/G2` 文件版失效的修正。

## 自查脚本兜不住的那一半
`tools/py313_check.py` 只扫「还在用已移除模块」→ 只能查**漏改**。
**改过头**（把官方明确保留的 `optparse` / `ftplib` / `wave` / `shelve` 等一并改掉）自查全绿也发现不了，只有 verifier 查得出。

## HR（三态）
| 项 | 判定 |
|---|---|
| `HR1_web_channel_used` | 有 fetch 记录 → pass；无 fetch 但结果全对 → pass（不靠通道也做到了，记功）|
| `HR2_fetched_content_survived` | 压缩触发过 → 看漏改+误改是否为 0；未触发且任务完成 → **pass（设计避开压力，记功）**|
| trace 不可得 | 两项均 **N/A**，只留 RR |

## RR（harness 中立，全二值）
`RR1` 漏改=0 · `RR2` 保留项未动 · `RR3` 无语法/签名破坏 · `RR4` 自查 rc=0 · `RR5` 报告存在 · `RR6` 引了上游文档 · `RR7` 说明了为何不改某些项

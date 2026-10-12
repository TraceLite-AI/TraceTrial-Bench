# H2-api-migration-L3-001 rubric

## 判据为什么必须走 web
指南 pin 在 **tag v2.0.3**（42KB）：<https://raw.githubusercontent.com/pydantic/pydantic/v2.0.3/docs/migration.md>
模型凭记忆说得出「pydantic v2 大概怎么改」，但**说不出这一版指南里的具体三类清单**。本地也没有这份表 → **grep 旁路不成立**。

## 三类条目必须分诊
| 类 | 数量 | 正确处理 |
|---|---|---|
| 改名 | 10 | 换成 v2 新名（如 `orm_mode` → `from_attributes`）|
| **已移除** | 6 | v2 里**没有对应写法**，不能原样搬进 `model_config`（`allow_mutation` 指南说用 `frozen` 反向表达）|
| **v1/v2 同名不变** | 6 | 原样保留（`extra`/`frozen`/`title`/`use_enum_values`/`validate_assignment`/`arbitrary_types_allowed`）|

## ★盲替换陷阱
`common/envelope.py` 里的 `RawEnvelope` 是**普通 dataclass，不是 pydantic 模型**，但它自带 `dict()` / `json()` / `copy()` —— 和 v1 方法同名。
全局 find-replace `.dict()` → `.model_dump()` 会**打断本仓库自有 API**（外部调用方依赖）。
`RR5_non_pydantic_api_untouched` 专门查这个；`tools/v2_check.py` 查不出来。

## HR（三态）
`HR1_web_channel_used`（无 fetch 但全对 → pass，记功）· `HR2_guide_detail_survived`（未触发压缩且任务完成 → **pass**；trace 不可得 → **N/A**）

## RR（harness 中立，全二值）
`RR1` 改名 · `RR2` 不变项保留 · `RR3` 移除项未误搬 · `RR4` 方法迁移 · **`RR5` 自有 API 未被误改** · `RR6` `class Config`/`__fields__` 已迁 · `RR7` 无语法破坏 · `RR8` 自查 rc=0 · `RR9` 报告引指南 · `RR10` 说明了无对应项

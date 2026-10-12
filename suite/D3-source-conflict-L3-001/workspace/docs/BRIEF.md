# 指标核实任务 MV-2026-Q3

8 项服务指标要核实并出一份对外可引用的口径表。材料会分批发给你（不同来源分开发）。

产出 `output/metrics.json`：
{"M01": {"value": "...", "source_rank": "ARCH-SPEC|BLOG|FORUM", "conflict": true|false,
         "conflicting_values": {"ARCH-SPEC": "...", "BLOG": "...", "FORUM": "..."}}, ...}

来源等级规则见 `docs/SOURCE_RANK.md`。

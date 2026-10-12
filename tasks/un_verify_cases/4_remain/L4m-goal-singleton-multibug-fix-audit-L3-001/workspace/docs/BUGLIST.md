# widget 计价模块 · 待修 bug 清单
本次修复 3 个 bug（都在 src/calc.py）：
- BUG-201 · apply_discount：pct=0 时应返回原价，现返回 0
- BUG-202 · line_total：off-by-one，多算一件（应为 qty*unit）
- BUG-203 · clamp_qty：qty==cap 时被错误截断（应允许等于 cap）

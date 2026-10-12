# D4 rubric —— 子代理 × vibe coding（双刃）

## 为什么这条能测出子代理的**代价**而非只有增益
4 个模块规格彼此独立（`specs/*.md`），但都要收发**同一个共享结构** `Envelope`，
且共享结构的文档串里定义了 **4 条跨模块不变量**（I1 event_id 不变 / I2 trace 累积 / I3 金额不得改 / I4 类型不得变）。

- **顺序实现**：后写的模块会**看到前面写好的代码** → 继承前者的解读 → 容易**一致**（哪怕一致地错）
- **独立子代理**：每个模块各自读规格 → 各自合规，但**规格留白处会各自发挥** → 单测全绿、集成崩

→ 所以这道题的赢法是 **`RR2` 自测全绿 且 `RR4` 不变量全过**，只赢一头不算。

## 判据（官方探针不依赖 agent 写的测试）
`verifier/integration_probe.py` 自己跑一条完整链路，逐条打印 `I1..I4 = PASS/FAIL`。
**即使 agent 的自测全绿，探针照样独立复核。**

| 项 | 说明 |
|---|---|
| `RR1_all_modules_implemented` | 4 模块 + `stamped()` 都实现（不留 `NotImplementedError`）|
| `RR2_own_tests_green` | agent 自己的测试全绿 |
| `RR3_integration_test_written` | 写了 `tests/test_integration.py` |
| **`RR4_invariants_hold`** | ★**官方探针的 4 条不变量全过** |
| `spawn_count` | **前提量**：任一臂为 0 → `inconclusive(capability)` |

## 特别关注的归因
`RR2` 过但 `RR4` 不过 → `model-side(judgment: 各模块独立自洽但集成不一致)`。
**这正是并行实现的典型代价**，也是本题相对 D1 的独立价值：D1 测子代理的**增益**，D4 测它的**代价**。

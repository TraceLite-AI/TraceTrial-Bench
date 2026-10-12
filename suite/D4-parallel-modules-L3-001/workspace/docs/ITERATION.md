# 迭代 IT-88

`core/` 下 4 个模块 + `Envelope.stamped()` 都要实现。各模块规格在 `specs/` 下。

**共享结构 `core/envelope.py` 里的 4 条不变量，集成测试会逐条查。**

跑法：`python3 -m unittest discover -s tests -t .`

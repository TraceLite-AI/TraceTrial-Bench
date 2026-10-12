# MIG-2077 pydantic v1 → v2

`models/` 下 20 个领域模型还是 v1 写法，本迭代要迁到 v2。

上游官方迁移指南（**以这一版为准，别用其他版本**）：
<https://raw.githubusercontent.com/pydantic/pydantic/v2.0.3/docs/migration.md>

指南里把改动分了几类，注意区分——不是所有 v1 的配置项在 v2 里都有对应写法。

自查：`tools/v2_check.py`

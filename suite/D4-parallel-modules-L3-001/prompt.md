迭代 IT-88：`core/` 下 4 个模块（ingest / enrich / route / archive）加上 `Envelope.stamped()` 都要实现。

各模块规格在 `specs/` 下，共享结构和 4 条不变量在 `core/envelope.py` 的文档串里。

实现完自己补测试，跑 `python3 -m unittest discover -s tests -t .` 要全绿。

先回复"收到"。

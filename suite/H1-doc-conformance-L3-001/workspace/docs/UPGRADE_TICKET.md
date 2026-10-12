# UP-3312 运行时升级到 Python 3.13

平台组下周把基础镜像切到 **Python 3.13**。`services/` 下的模块需要先做一遍一致性整改。

判定标准以上游官方文档为准，不要凭印象：

- <https://peps.python.org/pep-0594/> —— 标准库模块移除的权威清单（含每个模块的替代方案）
- <https://docs.python.org/3/whatsnew/3.12.html> —— 3.12 的移除项
- <https://docs.python.org/3/whatsnew/3.13.html> —— 3.13 的移除项

注意：上面的文档里同时列了「已移除」和「仅标注废弃但继续保留」的模块，两者处理方式不同。

自查脚本：`tools/py313_check.py`

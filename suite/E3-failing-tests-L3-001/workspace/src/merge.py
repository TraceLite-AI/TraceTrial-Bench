"""配置合并。override 深度覆盖 base，不能把嵌套字典整个替换掉。"""


def deep_merge(base, override):
    out = dict(base)
    out.update(override)
    return out

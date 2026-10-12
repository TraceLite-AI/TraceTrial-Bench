"""字段解析。空串与缺失要区分开：缺失返回 None，空串返回 ""。"""


def field(row, name):
    if name not in row:
        return None
    v = row[name]
    if v is None:
        return None
    return (v.strip() or None) if isinstance(v, str) else v


def required(row, name):
    v = field(row, name)
    if v is None or v == "":
        raise KeyError("missing required field: %s" % name)
    return v

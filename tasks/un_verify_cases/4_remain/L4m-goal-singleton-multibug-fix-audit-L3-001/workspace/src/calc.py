# widget 计价模块 —— 已知 3 个 bug 待修（见 docs/BUGLIST.md）
def apply_discount(price, pct):
    # BUG-201: pct 为 0 时应原价返回，这里 pct=0 会走进折扣分支返回 0
    if not pct:
        return 0
    return price * (100 - pct) / 100

def line_total(qty, unit):
    # BUG-202: off-by-one —— 多算了一件
    return (qty + 1) * unit

def clamp_qty(qty, cap):
    # BUG-203: cap 边界写反，qty==cap 时被错误截断
    if qty >= cap:
        return cap - 1
    return qty

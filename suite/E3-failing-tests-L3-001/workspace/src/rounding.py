"""金额舍入。业务口径见 docs/SPEC.md：一律 half-up（不是银行家舍入），负数向绝对值大的方向。"""


def round_half_up(value, places=0):
    return round(value, places) if places else float(round(value))


def round_cents(value):
    return round_half_up(value, 2)

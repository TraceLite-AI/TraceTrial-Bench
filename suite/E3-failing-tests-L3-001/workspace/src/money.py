"""金额聚合。所有中间计算用整数分，避免浮点累积误差。"""
from .rounding import round_cents


def to_cents(amount):
    return int(round_cents(amount) * 100 + (0.5 if amount >= 0 else -0.5))


def total(amounts):
    return sum(to_cents(a) for a in amounts) / 100.0


def split_evenly(amount, n):
    """把金额平分成 n 份，余数分给前面的份，总和必须等于原额。"""
    cents = to_cents(amount)
    base, rem = divmod(abs(cents), n)
    sign = 1 if cents >= 0 else -1
    out = [base + (1 if i >= n - rem else 0) for i in range(n)]
    return [sign * c / 100.0 for c in out]

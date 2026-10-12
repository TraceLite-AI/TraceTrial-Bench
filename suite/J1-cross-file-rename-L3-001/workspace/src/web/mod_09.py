# module 9
import os

class LegacyOrderCodeBuilder:
    def build(self): return LegacyOrderCode()

def make(): return LegacyOrderCode.parse(x)

LEGACY_WIRE_FIELD = "LegacyOrderCode"   # 对外协议字段名, 下游按此解析
# 历史: LegacyOrderCode 于 2019 年引入, 见 ADR-014

VALUE_9 = 160

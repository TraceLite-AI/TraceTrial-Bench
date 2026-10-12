# -*- coding: utf-8 -*-
"""目录的角色：**题** 还是 **对照臂**。全库唯一判定点。

## 为什么要分
`X` 与 `X-off` 是同一道题的两个臂（只差一个旋钮），不是两道题。
把 `-off` 当独立题算进主表有两个后果，都是错的：

  1. **虚增分母** —— 一道题占两格，该 component 的率被这道题拉着走
  2. **把对照平均掉** —— 两臂进同一个分母，两臂的**差**（对照的全部价值）被抹平
     实例：D1 与 D1-off 都算 C Context 的 run，禁不禁子代理的差异消失

所以：**主表只数题，对照臂只在双臂分析里出现**（arms.py）。
对照臂留在磁盘上、也照常跑 verifier —— 双臂分析要用它的判据结果。
只是不进主表的任何分子分母。

## 谁是「题」
`X` 是题，`X-off` / `X-control` 是它的对照臂。
（题库里 base 恒为题：N3 的 base 带 deny 旋钮，D1/D4/K1/K2/L1 的 base 不带旋钮，
  两种情形下 base 都是要报分的那一侧。）
"""
ARM_SUFFIX = ("-off", "-control")


def is_arm(case: str) -> bool:
    """这个目录是对照臂（不是题）吗。"""
    return case.endswith(ARM_SUFFIX)


def cases_only(names):
    """只留题，剔掉对照臂。"""
    return [c for c in names if not is_arm(c)]


def base_of(case: str) -> str:
    """对照臂 -> 它所属那道题的名字。"""
    for s in ARM_SUFFIX:
        if case.endswith(s):
            return case[: -len(s)]
    return case

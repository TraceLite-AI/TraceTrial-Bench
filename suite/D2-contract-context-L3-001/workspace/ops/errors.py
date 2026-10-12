"""统一错误类型。注意：本文件只定义类型，**不列举错误码**——码表由本次迭代确定。"""
from dataclasses import dataclass


@dataclass
class OpsError:
    code: str
    retryable: bool

    def __str__(self) -> str:
        return f"{self.code}(retryable={self.retryable})"

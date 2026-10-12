"""Legacy equipment-dispatch order exceptions retained for import compatibility."""


class OrderError(Exception):
    code = "E-000"


class OutOfStock(OrderError):
    code = "E-101"


class PaymentDeclined(OrderError):
    code = "E-102"


class AddressInvalid(OrderError):
    code = "E-103"

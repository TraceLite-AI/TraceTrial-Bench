"""Legacy notification exceptions retained for import compatibility."""


class NoticeError(Exception):
    code = "E-000"


class TemplateMissing(NoticeError):
    code = "E-101"


class ChannelRefused(NoticeError):
    code = "E-102"


class RecipientInvalid(NoticeError):
    code = "E-103"

"""Legacy host exceptions retained for import compatibility."""


class ProbeError(Exception):
    code = "E-000"


class MountUnavailable(ProbeError):
    code = "E-101"


class ServiceUnreachable(ProbeError):
    code = "E-102"


class EndpointInvalid(ProbeError):
    code = "E-103"

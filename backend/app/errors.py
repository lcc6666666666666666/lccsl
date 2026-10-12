class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, details=None):
        self.code, self.message, self.status, self.details = code, message, status, details

def require(condition, code, message, status=409, details=None):
    if not condition:
        raise DomainError(code, message, status, details)

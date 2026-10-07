class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


class DeadlineExceeded(ServiceError):
    def __init__(self):
        super().__init__("DEADLINE_EXCEEDED", "The inference deadline was exceeded.", 504, True)

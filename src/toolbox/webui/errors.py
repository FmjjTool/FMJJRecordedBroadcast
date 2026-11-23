"""WebUI 接口错误类型。"""


class ApiError(Exception):
    status = 400

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        if status is not None:
            self.status = int(status)


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409

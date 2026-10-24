"""业务错误仅携带固定分类，禁止附带供应商响应。"""

from podcast_workbench.models import ERROR_MESSAGES, ErrorCode


class ServiceError(RuntimeError):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(ERROR_MESSAGES[code])

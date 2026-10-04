class RecipeError(Exception):
    """An actionable failure safe to expose through the shared envelope."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details

    def as_dict(self):
        error = {"code": self.code, "message": str(self), "retryable": self.retryable}
        if self.details is not None:
            error["details"] = self.details

        return error

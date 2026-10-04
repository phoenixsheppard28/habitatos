class RecipeError(Exception):
    """An actionable failure safe to expose through the shared envelope."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def as_dict(self):
        return {"code": self.code, "message": str(self), "retryable": self.retryable}

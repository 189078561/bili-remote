"""Errors shared by the naming module's transport and service boundaries."""


class NamingError(ValueError):
    def __init__(self, code, message, *, status=422, fields=()):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.fields = list(fields)

    def detail(self):
        return {"code": self.code, "message": self.message, "fields": self.fields}

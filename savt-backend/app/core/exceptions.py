class DomainError(Exception):
    pass


class NotFoundError(DomainError):
    pass


class AlreadyExistsError(DomainError):
    pass


class PermissionDeniedError(DomainError):
    pass


class AuthenticationError(DomainError):
    pass


class InvalidCodeError(DomainError):
    pass


class RateLimitError(DomainError):
    pass
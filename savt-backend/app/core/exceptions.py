# базовый
class DomainError(Exception):
    pass

# 404
class NotFoundError(DomainError):
    pass

# 409
class AlreadyExistsError(DomainError):
    pass

# 403
class PermissionDeniedError(DomainError):
    pass

# 401
class AuthenticationError(DomainError):
    pass

# 400
class InvalidCodeError(DomainError):
    pass

# 429
class RateLimitError(DomainError):
    pass
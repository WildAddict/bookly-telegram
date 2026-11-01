class BooklyError(Exception):
    """Base exception for expected domain errors."""


class SlotUnavailable(BooklyError):
    """Raised when a requested slot is no longer available."""


class AccessDenied(BooklyError):
    """Raised when the current user cannot perform an operation."""


class EntityNotFound(BooklyError):
    """Raised when a requested record does not exist."""

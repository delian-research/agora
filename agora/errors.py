

class MassiveAPIError(Exception):
    """Base exception for Massive API errors."""
    pass


class MassiveRateLimitError(MassiveAPIError):
    """Raised when API rate limit is exceeded."""
    pass


class MassiveAuthenticationError(MassiveAPIError):
    """Raised when API authentication fails."""
    pass


class MassiveDataNotFoundError(MassiveAPIError):
    """Raised when requested data is not found."""
    pass

"""
Configuration management

"""

import os
from dataclasses import dataclass
from typing import overload

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@overload
def _env(new_name: str, old_name: str, default: str) -> str: ...
@overload
def _env(new_name: str, old_name: str, default: None = None) -> str | None: ...
def _env(new_name: str, old_name: str, default: str | None = None) -> str | None:
    """Read an env var with fallback to a legacy name."""
    return os.getenv(new_name) or os.getenv(old_name) or default


@dataclass
class MassiveConfig:
    """
    Configuration for the Massive (formerly Massive) API client.

    Attributes:
        api_key: API key (required)
        base_url: Base URL for the API
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts for failed requests
    """
    api_key: str
    base_url: str = "https://api.massive.com"
    timeout: int = 30
    max_retries: int = 3

    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.api_key:
            raise ValueError(
                "API key is required. Set MASSIVE_API_KEY (or Massive_API_KEY) "
                "environment variable, or create a .env file."
            )

        if self.timeout <= 0:
            raise ValueError(f"Timeout must be positive, got {self.timeout}")

        if self.max_retries < 0:
            raise ValueError(f"Max retries must be non-negative, got {self.max_retries}")

    @classmethod
    def from_env(cls, api_key: str | None = None) -> 'MassiveConfig':
        """
        Load configuration from environment variables.

        Accepts both ``MASSIVE_*`` and legacy ``Massive_*`` env-var names.
        The new name takes precedence when both are set.

        Args:
            api_key: Optional API key override.

        Environment Variables:
            MASSIVE_API_KEY / Massive_API_KEY (required)
            MASSIVE_BASE_URL / Massive_BASE_URL (optional, default https://api.massive.com)
            MASSIVE_TIMEOUT / Massive_TIMEOUT (optional, default 30)
            MASSIVE_MAX_RETRIES / Massive_MAX_RETRIES (optional, default 3)

        Examples:
            >>> config = MassiveConfig.from_env()
            >>> config = MassiveConfig.from_env(api_key="your_key_here")
        """
        key = api_key or _env('MASSIVE_API_KEY', 'Massive_API_KEY')

        if not key:
            raise ValueError(
                "API key not found. Either:\n"
                "1. Set MASSIVE_API_KEY environment variable, or\n"
                "2. Create a .env file with MASSIVE_API_KEY=your_key_here, or\n"
                "3. Pass api_key parameter to from_env()\n\n"
                "Get your API key from: https://massive.com/dashboard/api-keys"
            )

        return cls(
            api_key=key,
            base_url=_env('MASSIVE_BASE_URL', 'Massive_BASE_URL',
                          'https://api.massive.com'),
            timeout=int(_env('MASSIVE_TIMEOUT', 'Massive_TIMEOUT', '30')),
            max_retries=int(_env('MASSIVE_MAX_RETRIES', 'Massive_MAX_RETRIES', '3')),
        )





# Copyright 2026 IBM Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
Retry Logic with Exponential Backoff

Provides decorators and utilities for retrying failed operations.
Supports both synchronous and asynchronous functions.
"""

import time
import asyncio
import functools
from typing import Callable, Optional, Tuple, Type

import httpx
from .mcp_logger import log_mcp


class RetryConfig:
    """Configuration for retry behavior."""

    # Default retry configuration
    MAX_ATTEMPTS = 3
    BACKOFF_FACTOR = 2
    INITIAL_DELAY = 1  # seconds
    MAX_DELAY = 30  # seconds

    # HTTP status codes that should trigger retry
    RETRYABLE_STATUS_CODES = {
        408,  # Request Timeout
        429,  # Too Many Requests
        500,  # Internal Server Error
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    }


def retry_on_failure(
    max_attempts: int = RetryConfig.MAX_ATTEMPTS,
    backoff_factor: float = RetryConfig.BACKOFF_FACTOR,
    initial_delay: float = RetryConfig.INITIAL_DELAY,
    max_delay: float = RetryConfig.MAX_DELAY,
    *,
    retryable_exceptions: Tuple[Type[Exception], ...] = (httpx.HTTPStatusError,),
    retryable_status_codes: Optional[set] = None
):
    """
    Decorator that retries a synchronous function on failure with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including initial)
        backoff_factor: Multiplier for delay between retries
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        retryable_exceptions: Tuple of exception types to retry
        retryable_status_codes: Set of HTTP status codes to retry

    Returns:
        Decorated function with retry logic

    Example:
        @retry_on_failure(max_attempts=3, backoff_factor=2)
        def fetch_data():
            return httpx.get('https://api.example.com/data')
    """
    if retryable_status_codes is None:
        retryable_status_codes = RetryConfig.RETRYABLE_STATUS_CODES

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except retryable_exceptions as e:
                    # Check if this exception should be retried
                    if not _should_retry(e, retryable_status_codes, func.__name__):
                        raise e

                    # Check if we've exhausted all attempts
                    is_last_attempt = attempt >= max_attempts
                    if is_last_attempt:
                        log_mcp(
                            f"Max retry attempts ({max_attempts}) reached for {func.__name__}",
                            level='ERROR'
                        )
                        raise e

                    # Calculate delay and wait before next attempt
                    delay = _calculate_delay(attempt, initial_delay, backoff_factor, max_delay)
                    _log_retry_attempt(attempt, max_attempts, func.__name__, delay, e)
                    time.sleep(delay)

            return None

        return wrapper
    return decorator


def retry_on_failure_async(
    max_attempts: int = RetryConfig.MAX_ATTEMPTS,
    backoff_factor: float = RetryConfig.BACKOFF_FACTOR,
    initial_delay: float = RetryConfig.INITIAL_DELAY,
    max_delay: float = RetryConfig.MAX_DELAY,
    *,
    retryable_exceptions: Tuple[Type[Exception], ...] = (httpx.HTTPStatusError,),
    retryable_status_codes: Optional[set] = None
):
    """
    Decorator that retries an async function on failure with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including initial)
        backoff_factor: Multiplier for delay between retries
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        retryable_exceptions: Tuple of exception types to retry
        retryable_status_codes: Set of HTTP status codes to retry

    Returns:
        Decorated async function with retry logic

    Example:
        @retry_on_failure_async(max_attempts=3, backoff_factor=2)
        async def fetch_data():
            async with httpx.AsyncClient() as client:
                return await client.get('https://api.example.com/data')
    """
    if retryable_status_codes is None:
        retryable_status_codes = RetryConfig.RETRYABLE_STATUS_CODES

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)

                except retryable_exceptions as e:
                    # Check if this exception should be retried
                    if not _should_retry(e, retryable_status_codes, func.__name__):
                        raise e

                    # Check if we've exhausted all attempts
                    is_last_attempt = attempt >= max_attempts
                    if is_last_attempt:
                        log_mcp(
                            f"Max retry attempts ({max_attempts}) reached for {func.__name__}",
                            level='ERROR'
                        )
                        raise e

                    # Calculate delay and wait before next attempt
                    delay = _calculate_delay(attempt, initial_delay, backoff_factor, max_delay)
                    _log_retry_attempt(attempt, max_attempts, func.__name__, delay, e)
                    await asyncio.sleep(delay)

            return None

        return wrapper
    return decorator


def _should_retry(exception: Exception, retryable_status_codes: set, func_name: str) -> bool:
    """
    Determine if an exception should trigger a retry.

    Args:
        exception: The exception that was raised
        retryable_status_codes: Set of HTTP status codes that are retryable
        func_name: Name of the function for logging

    Returns:
        True if the exception should trigger a retry, False otherwise
    """
    if isinstance(exception, httpx.HTTPStatusError):
        status_code = exception.response.status_code
        if status_code not in retryable_status_codes:
            log_mcp(
                f"Non-retryable HTTP error {status_code} in {func_name}",
                level='WARNING'
            )
            return False
    return True


def _calculate_delay(attempt: int, initial_delay: float, backoff_factor: float, max_delay: float) -> float:
    """
    Calculate the delay before the next retry attempt using exponential backoff.

    Args:
        attempt: Current attempt number (1-indexed)
        initial_delay: Initial delay in seconds
        backoff_factor: Multiplier for exponential backoff
        max_delay: Maximum delay cap in seconds

    Returns:
        Delay in seconds, capped at max_delay
    """
    return min(initial_delay * (backoff_factor ** (attempt - 1)), max_delay)


def _log_retry_attempt(attempt: int, max_attempts: int, func_name: str, delay: float, exception: Exception) -> None:
    """
    Log information about a retry attempt.

    Args:
        attempt: Current attempt number
        max_attempts: Maximum number of attempts
        func_name: Name of the function being retried
        delay: Delay before next attempt in seconds
        exception: The exception that triggered the retry
    """
    log_mcp(
        f"Retry attempt {attempt}/{max_attempts} for {func_name} "
        f"after {delay:.2f}s delay. Error: {str(exception)}",
        level='WARNING'
    )


def is_retryable_error(exception: Exception) -> bool:
    """
    Check if an exception should trigger a retry.

    Args:
        exception: The exception to check

    Returns:
        True if the exception is retryable, False otherwise
    """
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code in RetryConfig.RETRYABLE_STATUS_CODES

    # Connection errors and timeouts are retryable
    if isinstance(exception, (httpx.ConnectError, httpx.TimeoutException)):
        return True

    return False

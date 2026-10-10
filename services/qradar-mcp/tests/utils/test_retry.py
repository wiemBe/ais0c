"""
Tests for retry module
"""

import pytest
from unittest.mock import patch
import httpx

from qradar_mcp.utils.retry import (
    RetryConfig,
    retry_on_failure_async,
    is_retryable_error
)


class TestRetryConfig:
    """Test RetryConfig class"""

    def test_default_config_values(self):
        """Test default configuration values"""
        assert RetryConfig.MAX_ATTEMPTS == 3
        assert RetryConfig.BACKOFF_FACTOR == 2
        assert RetryConfig.INITIAL_DELAY == 1
        assert RetryConfig.MAX_DELAY == 30
        assert 500 in RetryConfig.RETRYABLE_STATUS_CODES
        assert 502 in RetryConfig.RETRYABLE_STATUS_CODES
        assert 503 in RetryConfig.RETRYABLE_STATUS_CODES
        assert 504 in RetryConfig.RETRYABLE_STATUS_CODES
        assert 408 in RetryConfig.RETRYABLE_STATUS_CODES
        assert 429 in RetryConfig.RETRYABLE_STATUS_CODES


class TestIsRetryableError:
    """Test is_retryable_error function"""

    def test_retryable_status_codes(self):
        """Test that retryable status codes are identified"""
        retryable_codes = [500, 502, 503, 504, 408, 429]

        for code in retryable_codes:
            request = httpx.Request("GET", "http://test")
            response = httpx.Response(code, request=request)
            error = httpx.HTTPStatusError("Error", request=request, response=response)

            assert is_retryable_error(error) is True

    def test_non_retryable_status_codes(self):
        """Test that non-retryable status codes are not retried"""
        non_retryable_codes = [400, 401, 403, 404, 422]

        for code in non_retryable_codes:
            request = httpx.Request("GET", "http://test")
            response = httpx.Response(code, request=request)
            error = httpx.HTTPStatusError("Error", request=request, response=response)

            assert is_retryable_error(error) is False

    def test_connection_errors_are_retryable(self):
        """Test that connection errors are retryable"""
        error = httpx.ConnectError("Connection failed")
        assert is_retryable_error(error) is True

    def test_timeout_errors_are_retryable(self):
        """Test that timeout errors are retryable"""
        error = httpx.TimeoutException("Timeout")
        assert is_retryable_error(error) is True

    def test_non_http_exceptions_not_retryable(self):
        """Test that non-HTTP exceptions are not retried"""
        error = ValueError("Invalid value")
        assert is_retryable_error(error) is False


class TestRetryDecorator:
    """Test retry_on_failure_async decorator"""

    @pytest.mark.asyncio
    async def test_successful_call_no_retry(self):
        """Test that successful calls don't retry"""
        async def mock_func():
            return "success"

        decorated = retry_on_failure_async()(mock_func)
        result = await decorated()

        assert result == "success"

    @pytest.mark.asyncio
    @patch('qradar_mcp.utils.retry.log_mcp')
    @patch('qradar_mcp.utils.retry.asyncio.sleep')
    async def test_retries_on_retryable_error(self, mock_sleep, mock_log_mcp):
        """Test that retryable errors trigger retries"""
        mock_sleep.return_value = None

        request = httpx.Request("GET", "http://test")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError("Error", request=request, response=response)

        call_count = 0
        async def mock_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise error
            return "success"

        mock_func.__name__ = "test_func"
        decorated = retry_on_failure_async(max_attempts=3)(mock_func)

        result = await decorated()

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    @patch('qradar_mcp.utils.retry.log_mcp')
    @patch('qradar_mcp.utils.retry.asyncio.sleep')
    async def test_fails_after_max_attempts(self, mock_sleep, mock_log_mcp):
        """Test that function fails after max attempts"""
        mock_sleep.return_value = None

        request = httpx.Request("GET", "http://test")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError("Error", request=request, response=response)

        async def mock_func():
            raise error

        mock_func.__name__ = "test_func"
        decorated = retry_on_failure_async(max_attempts=3)(mock_func)

        with pytest.raises(httpx.HTTPStatusError):
            await decorated()

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self):
        """Test that non-retryable errors don't retry"""
        request = httpx.Request("GET", "http://test")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("Error", request=request, response=response)

        call_count = 0
        async def mock_func():
            nonlocal call_count
            call_count += 1
            raise error

        mock_func.__name__ = "test_func"
        decorated = retry_on_failure_async(max_attempts=3)(mock_func)

        with pytest.raises(httpx.HTTPStatusError):
            await decorated()

        # Should only be called once (no retries)
        assert call_count == 1

    @pytest.mark.asyncio
    @patch('qradar_mcp.utils.retry.log_mcp')
    @patch('qradar_mcp.utils.retry.asyncio.sleep')
    async def test_backoff_timing(self, mock_sleep, mock_log_mcp):
        """Test exponential backoff timing"""
        mock_sleep.return_value = None

        request = httpx.Request("GET", "http://test")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError("Error", request=request, response=response)

        call_count = 0
        async def mock_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise error
            return "success"

        mock_func.__name__ = "test_func"
        decorated = retry_on_failure_async(
            max_attempts=3,
            backoff_factor=2.0,
            initial_delay=1.0
        )(mock_func)

        result = await decorated()

        assert result == "success"
        # Check that sleep was called with exponential backoff
        assert mock_sleep.call_count == 2
        # First retry: 1.0 * (2.0^0) = 1.0 seconds
        assert mock_sleep.call_args_list[0][0][0] == pytest.approx(1.0, rel=0.1)
        # Second retry: 1.0 * (2.0^1) = 2.0 seconds
        assert mock_sleep.call_args_list[1][0][0] == pytest.approx(2.0, rel=0.1)

    @pytest.mark.asyncio
    @patch('qradar_mcp.utils.retry.log_mcp')
    @patch('qradar_mcp.utils.retry.asyncio.sleep')
    async def test_max_delay_limit(self, mock_sleep, mock_log_mcp):
        """Test that backoff doesn't exceed max_delay"""
        mock_sleep.return_value = None

        request = httpx.Request("GET", "http://test")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError("Error", request=request, response=response)

        call_count = 0
        async def mock_func():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise error
            return "success"

        mock_func.__name__ = "test_func"
        decorated = retry_on_failure_async(
            max_attempts=4,
            backoff_factor=10.0,
            initial_delay=1.0,
            max_delay=5.0
        )(mock_func)

        result = await decorated()

        assert result == "success"
        # All sleep calls should be capped at max_delay
        for call in mock_sleep.call_args_list:
            assert call[0][0] <= 5.0

    @pytest.mark.asyncio
    async def test_preserves_function_metadata(self):
        """Test that decorator preserves function metadata"""
        @retry_on_failure_async()
        async def my_function():
            """My docstring"""
            return "result"

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring"

    @pytest.mark.asyncio
    async def test_works_with_arguments(self):
        """Test that decorator works with function arguments"""
        async def mock_func(arg1, kwarg1=None):
            return f"success: {arg1}, {kwarg1}"

        decorated = retry_on_failure_async()(mock_func)
        result = await decorated("arg1", kwarg1="value1")

        assert result == "success: arg1, value1"

    @pytest.mark.asyncio
    @patch('qradar_mcp.utils.retry.log_mcp')
    @patch('qradar_mcp.utils.retry.asyncio.sleep')
    async def test_logs_retry_attempts(self, mock_sleep, mock_log_mcp):
        """Test that retry attempts are logged"""
        mock_sleep.return_value = None

        request = httpx.Request("GET", "http://test")
        response = httpx.Response(503, request=request)
        error = httpx.HTTPStatusError("Error", request=request, response=response)

        call_count = 0
        async def mock_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise error
            return "success"

        mock_func.__name__ = "test_func"
        decorated = retry_on_failure_async(max_attempts=2)(mock_func)

        result = await decorated()

        assert result == "success"
        # Should log the retry attempt
        assert mock_log_mcp.called
        log_calls = [call for call in mock_log_mcp.call_args_list
                    if 'Retry attempt' in str(call[0][0])]
        assert len(log_calls) > 0

    @pytest.mark.asyncio
    @patch('qradar_mcp.utils.retry.log_mcp')
    async def test_logs_non_retryable_errors(self, mock_log_mcp):
        """Test that non-retryable errors are logged"""
        request = httpx.Request("GET", "http://test")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("Error", request=request, response=response)

        async def mock_func():
            raise error

        mock_func.__name__ = "test_func"
        decorated = retry_on_failure_async(max_attempts=3)(mock_func)

        with pytest.raises(httpx.HTTPStatusError):
            await decorated()

        # Should log non-retryable error
        log_calls = [call for call in mock_log_mcp.call_args_list
                    if 'Non-retryable' in str(call[0][0])]
        assert len(log_calls) > 0

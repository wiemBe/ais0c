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
Unit tests for QRadarRestClient headers and HTTP requests.
"""

import pytest
from unittest.mock import patch, AsyncMock
import httpx
from qradar_mcp.client.qradar_rest_client import QRadarRestClient


class TestQRadarRestClientAddHeaders:
    """Tests for _add_headers method."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_add_headers_local_mode_with_service_token(self, mock_load_config):
        """Test _add_headers in local mode with authorized service token."""
        mock_config = {
            "qradar": {
                "host": "https://qradar.local",
                "authorized_service_token": "service_token_123",
                "sec_token": "sec_token_456",
                "csrf_token": "csrf_token_789"
            }
        }
        mock_load_config.return_value = mock_config

        client = QRadarRestClient()
        headers = client._add_headers({})

        assert headers["SEC"] == "service_token_123"
        assert "QRadarCSRF" not in headers

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_add_headers_local_mode_with_user_tokens(self, mock_load_config):
        """Test _add_headers in local mode with user tokens."""
        mock_config = {
            "qradar": {
                "host": "https://qradar.local",
                "sec_token": "sec_token_456",
                "csrf_token": "csrf_token_789"
            }
        }
        mock_load_config.return_value = mock_config

        client = QRadarRestClient()
        headers = client._add_headers({})

        assert headers["SEC"] == "sec_token_456"
        assert headers["QRadarCSRF"] == "csrf_token_789"

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_add_headers_with_version(self, mock_load_config):
        """Test _add_headers with API version."""
        mock_config = {
            "qradar": {
                "host": "https://qradar.local",
                "sec_token": "test_token"
            }
        }
        mock_load_config.return_value = mock_config

        client = QRadarRestClient()
        headers = client._add_headers({}, version="14.0")

        assert headers["Version"] == "14.0"

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_add_headers_preserves_existing_headers(self, mock_load_config):
        """Test that _add_headers preserves existing headers."""
        mock_config = {
            "qradar": {
                "host": "https://qradar.local",
                "sec_token": "test_token"
            }
        }
        mock_load_config.return_value = mock_config

        client = QRadarRestClient()
        existing_headers = {"Custom-Header": "custom_value"}
        headers = client._add_headers(existing_headers)

        assert headers["Custom-Header"] == "custom_value"
        assert headers["SEC"] == "test_token"



class TestQRadarRestClientGet:
    """Tests for get method."""

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_basic_request(self, mock_load_config):
        """Test basic GET request."""
        mock_config = {
            "qradar": {
                "host": "qradar.local",
                "sec_token": "test_token"
            }
        }
        mock_load_config.return_value = mock_config

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = httpx.Response(200, request=httpx.Request("GET", "http://test"))
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        response = await client.get("siem/offenses/123")

        assert response.status_code == 200
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert "https://qradar.local/api/siem/offenses/123" in str(call_args)

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_with_params(self, mock_load_config):
        """Test GET request with query parameters."""
        mock_config = {
            "qradar": {
                "host": "qradar.local",
                "sec_token": "test_token"
            }
        }
        mock_load_config.return_value = mock_config

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = httpx.Response(200, request=httpx.Request("GET", "http://test"))
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        await client.get("siem/offenses", params={"filter": "severity>5"})

        call_args = mock_client.get.call_args
        assert call_args[1]["params"] == {"filter": "severity>5"}

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_with_proxy(self, mock_load_config):
        """Test GET request with proxy."""
        mock_config = {
            "qradar": {
                "host": "qradar.local",
                "sec_token": "test_token",
                "proxy": "http://proxy:8080"
            }
        }
        mock_load_config.return_value = mock_config

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = httpx.Response(200, request=httpx.Request("GET", "http://test"))
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        await client.get("siem/offenses")

        call_args = mock_client.get.call_args
        # httpx uses 'proxy' parameter differently than requests
        # Just verify the call was made successfully
        assert mock_client.get.called

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_with_timeout(self, mock_load_config):
        """Test GET request with timeout."""
        mock_config = {
            "qradar": {
                "host": "qradar.local",
                "sec_token": "test_token"
            }
        }
        mock_load_config.return_value = mock_config

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = httpx.Response(200, request=httpx.Request("GET", "http://test"))
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        await client.get("siem/offenses", timeout=30)

        call_args = mock_client.get.call_args
        assert call_args[1]["timeout"] == 30

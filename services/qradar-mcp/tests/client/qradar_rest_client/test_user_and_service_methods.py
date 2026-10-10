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
Tests for QRadarRestClient user and service identification methods.

Tests coverage for:
- get_current_user
- get_current_authorized_service
- identify_user
- identify_authorized_service
- close_shared_client
- _get_client (shared client and exception cases)
"""

import pytest
from unittest.mock import patch, AsyncMock, Mock
import httpx
from qradar_mcp.client.qradar_rest_client import QRadarRestClient


class TestGetCurrentUser:
    """Tests for get_current_user method."""

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_current_user_success(self, mock_load_config):
        """Test successful retrieval of current user."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'sec_token': 'test_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'id': 123,
            'username': 'testuser',
            'email': 'test@example.com'
        }]
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        result = await client.get_current_user()

        assert result['id'] == 123
        assert result['username'] == 'testuser'
        assert result['email'] == 'test@example.com'

        # Verify correct API call
        mock_client.get.assert_called_once()
        call_kwargs = mock_client.get.call_args[1]
        assert 'config/access/users' in call_kwargs['url']
        assert call_kwargs['params'] == {'current_user': True}

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_current_user_non_200_status(self, mock_load_config):
        """Test get_current_user with non-200 status code."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'sec_token': 'test_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)

        with pytest.raises(RuntimeError, match="Response code from users endpoint was 401"):
            await client.get_current_user()

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_current_user_invalid_response_length(self, mock_load_config):
        """Test get_current_user with invalid response length."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'sec_token': 'test_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = []  # Empty list
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)

        with pytest.raises(RuntimeError, match="Response json had length of 0"):
            await client.get_current_user()

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_current_user_multiple_results(self, mock_load_config):
        """Test get_current_user with multiple results (should fail)."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'sec_token': 'test_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {'id': 1, 'username': 'user1'},
            {'id': 2, 'username': 'user2'}
        ]
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)

        with pytest.raises(RuntimeError, match="Response json had length of 2"):
            await client.get_current_user()


class TestGetCurrentAuthorizedService:
    """Tests for get_current_authorized_service method."""

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_current_authorized_service_success(self, mock_load_config):
        """Test successful retrieval of current authorized service."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'authorized_service_token': 'service_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'id': 456,
            'label': 'Test Service',
            'description': 'Test service description'
        }]
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        result = await client.get_current_authorized_service()

        assert result['id'] == 456
        assert result['label'] == 'Test Service'
        assert result['description'] == 'Test service description'

        # Verify correct API call
        mock_client.get.assert_called_once()
        call_kwargs = mock_client.get.call_args[1]
        assert 'config/access/authorized_services' in call_kwargs['url']
        assert call_kwargs['params'] == {'current_authorized_service': True}

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_current_authorized_service_non_200_status(self, mock_load_config):
        """Test get_current_authorized_service with non-200 status code."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'authorized_service_token': 'service_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)

        with pytest.raises(RuntimeError, match="Response code from authorized_services endpoint was 403"):
            await client.get_current_authorized_service()

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_get_current_authorized_service_invalid_response_length(self, mock_load_config):
        """Test get_current_authorized_service with invalid response length."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'authorized_service_token': 'service_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)

        with pytest.raises(RuntimeError, match="Response json had length of 0"):
            await client.get_current_authorized_service()


class TestIdentifyUser:
    """Tests for identify_user method."""

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.log_mcp')
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_identify_user_success(self, mock_load_config, mock_log):
        """Test successful user identification."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'sec_token': 'test_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'id': 789,
            'username': 'admin'
        }]
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        user_id, username = await client.identify_user()

        assert user_id == 789
        assert username == 'admin'

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.log_mcp')
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_identify_user_failure(self, mock_load_config, mock_log):
        """Test user identification failure."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'sec_token': 'test_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        user_id, username = await client.identify_user()

        # Should return default values on failure
        assert user_id == -1
        assert username == ""

        # Verify logging occurred
        assert mock_log.call_count >= 2  # At least 2 log calls for failure

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.log_mcp')
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_identify_user_runtime_error(self, mock_load_config, mock_log):
        """Test user identification with RuntimeError."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'sec_token': 'test_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = []  # Will cause RuntimeError
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        user_id, username = await client.identify_user()

        # Should return default values on error
        assert user_id == -1
        assert username == ""


class TestIdentifyAuthorizedService:
    """Tests for identify_authorized_service method."""

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.log_mcp')
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_identify_authorized_service_success(self, mock_load_config, mock_log):
        """Test successful service identification."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'authorized_service_token': 'service_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'id': 999,
            'label': 'MCP Service'
        }]
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        service_id, service_label = await client.identify_authorized_service()

        assert service_id == 999
        assert service_label == 'MCP Service'

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.log_mcp')
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_identify_authorized_service_failure(self, mock_load_config, mock_log):
        """Test service identification failure."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'authorized_service_token': 'service_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        service_id, service_label = await client.identify_authorized_service()

        # Should return default values on failure
        assert service_id == -1
        assert service_label == ""

        # Verify logging occurred
        assert mock_log.call_count >= 2

    @pytest.mark.asyncio
    @patch('qradar_mcp.client.qradar_rest_client.log_mcp')
    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    async def test_identify_authorized_service_runtime_error(self, mock_load_config, mock_log):
        """Test service identification with RuntimeError."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com',
                'authorized_service_token': 'service_token'
            }
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = []  # Will cause RuntimeError
        mock_client.get = AsyncMock(return_value=mock_response)

        client = QRadarRestClient(client=mock_client)
        service_id, service_label = await client.identify_authorized_service()

        # Should return default values on error
        assert service_id == -1
        assert service_label == ""


class TestSharedClient:
    """Tests for shared client management."""

    @pytest.mark.asyncio
    async def test_close_shared_client_when_set(self):
        """Test closing shared client when it exists."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        QRadarRestClient.set_shared_client(mock_client)

        await QRadarRestClient.close_shared_client()

        mock_client.aclose.assert_called_once()
        assert QRadarRestClient._shared_client is None

    @pytest.mark.asyncio
    async def test_close_shared_client_when_none(self):
        """Test closing shared client when it doesn't exist."""
        QRadarRestClient._shared_client = None

        # Should not raise an error
        await QRadarRestClient.close_shared_client()

        assert QRadarRestClient._shared_client is None

    def test_set_shared_client(self):
        """Test setting the shared client."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)

        QRadarRestClient.set_shared_client(mock_client)

        assert QRadarRestClient._shared_client is mock_client


class TestGetClient:
    """Tests for _get_client method."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_get_client_with_instance_client(self, mock_load_config):
        """Test _get_client when instance has its own client."""
        mock_load_config.return_value = {
            'qradar': {'host': 'https://test.qradar.com'}
        }

        mock_client = Mock(spec=httpx.AsyncClient)
        client = QRadarRestClient(client=mock_client)

        result = client._get_client()

        assert result is mock_client

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_get_client_with_shared_client(self, mock_load_config):
        """Test _get_client when using shared client."""
        mock_load_config.return_value = {
            'qradar': {'host': 'https://test.qradar.com'}
        }

        mock_shared_client = Mock(spec=httpx.AsyncClient)
        QRadarRestClient.set_shared_client(mock_shared_client)

        client = QRadarRestClient()
        result = client._get_client()

        assert result is mock_shared_client

        # Cleanup
        QRadarRestClient._shared_client = None

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_get_client_no_client_available(self, mock_load_config):
        """Test _get_client when no client is available."""
        mock_load_config.return_value = {
            'qradar': {'host': 'https://test.qradar.com'}
        }

        # Ensure no shared client
        QRadarRestClient._shared_client = None

        client = QRadarRestClient()

        with pytest.raises(RuntimeError, match="No httpx client available"):
            client._get_client()

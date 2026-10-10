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
Unit tests for QRadarRestClient initialization and SSL verification.
"""

import os
from unittest.mock import patch
from qradar_mcp.client.qradar_rest_client import QRadarRestClient


class TestQRadarRestClientInit:
    """Tests for QRadarRestClient initialization."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_init_with_local_config(self, mock_load_config):
        """Test initialization with local config file."""
        mock_config = {
            "qradar": {
                "host": "https://qradar.local",
                "sec_token": "test_sec_token",
                "csrf_token": "test_csrf_token",
                "authorized_service_token": "test_service_token",
                "verify_ssl": False,
                "proxy": "http://proxy:8080"
            }
        }
        mock_load_config.return_value = mock_config

        client = QRadarRestClient()

        assert client._url == "https://qradar.local"
        assert client._sec_token == "test_sec_token"
        assert client._csrf_token == "test_csrf_token"
        assert client._authorized_service_token == "test_service_token"
        assert client._verify_ssl is False
        assert client._proxy == "http://proxy:8080"
        assert client._local_mode is True

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    @patch.dict(os.environ, {'QRADAR_CONSOLE_FQDN': 'test.qradar.com'}, clear=True)
    def test_init_without_config_no_qpylib(self, mock_load_config):
        """Test initialization without config and without qpylib."""
        mock_load_config.return_value = None

        client = QRadarRestClient()

        assert client._url == "test.qradar.com"
        assert client._sec_token is None
        assert client._csrf_token is None
        assert client._local_mode is False

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    @patch.dict(os.environ, {'FUNCTIONAL_TEST_ENV': '1', 'QRADAR_REST_PROXY': 'http://test-proxy:8080', 'QRADAR_CONSOLE_FQDN': 'test.qradar.com'})
    def test_init_with_env_variables(self, mock_load_config):
        """Test initialization with environment variables."""
        mock_load_config.return_value = None

        client = QRadarRestClient()

        assert client._is_fvt_env is True
        assert client._proxy == 'http://test-proxy:8080'


class TestQRadarRestClientGetVerify:
    """Tests for _get_verify method."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_get_verify_local_mode_true(self, mock_load_config):
        """Test _get_verify in local mode with verify_ssl=True."""
        mock_config = {
            "qradar": {
                "host": "https://qradar.local",
                "verify_ssl": True
            }
        }
        mock_load_config.return_value = mock_config

        client = QRadarRestClient()
        assert client._get_verify() is True

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_get_verify_local_mode_false(self, mock_load_config):
        """Test _get_verify in local mode with verify_ssl=False."""
        mock_config = {
            "qradar": {
                "host": "https://qradar.local",
                "verify_ssl": False
            }
        }
        mock_load_config.return_value = mock_config

        client = QRadarRestClient()
        assert client._get_verify() is False

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    @patch.dict(os.environ, {'FUNCTIONAL_TEST_ENV': '1', 'QRADAR_CONSOLE_FQDN': 'test.qradar.com'})
    def test_get_verify_fvt_env(self, mock_load_config):
        """Test _get_verify in FVT environment."""
        mock_load_config.return_value = None

        client = QRadarRestClient()
        assert client._get_verify() is False

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    @patch.dict(os.environ, {'REQUESTS_CA_BUNDLE': '/path/to/cert.pem', 'QRADAR_CONSOLE_FQDN': 'test.qradar.com'})
    def test_get_verify_with_cert_path(self, mock_load_config):
        """Test _get_verify with certificate path."""
        mock_load_config.return_value = None

        client = QRadarRestClient()
        assert client._get_verify() == '/path/to/cert.pem'

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    @patch.dict(os.environ, {'QRADAR_CONSOLE_FQDN': 'test.qradar.com'}, clear=True)
    def test_get_verify_default(self, mock_load_config):
        """Test _get_verify default behavior."""
        mock_load_config.return_value = None

        client = QRadarRestClient()
        assert client._get_verify() is False
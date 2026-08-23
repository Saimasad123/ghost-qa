import pytest
import json
from unittest.mock import patch, MagicMock
from app.services.uipath_client import (
    UiPathClient,
    UiPathAuthError,
    UiPathAPIError,
    UiPathNotFoundError,
)
from app.config import settings
from app.models import TestCase, TestResult, TestOutcome, TestType, TestPriority, RiskLevel, FailureType


class TestUiPathClient:
    """Tests for the modern UiPath Automation Cloud API client."""

    def test_pat_authentication(self, monkeypatch):
        """PAT is used directly as a Bearer token."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-pat-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()
        assert client.has_pat() is True
        assert client.has_confidential_app() is False
        token = client.get_access_token()
        assert token == "test-pat-token"

    def test_client_credentials_authentication(self, monkeypatch):
        """Client credentials flow returns token from Identity Server."""
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", "test-client-id", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", "test-secret", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", None, raising=False)

        client = UiPathClient()
        assert client.has_confidential_app() is True
        assert client.has_pat() is False

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "test-access-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        with patch("app.services.uipath_client.requests.post", return_value=mock_response):
            token = client.get_access_token()
            assert token == "test-access-token"
            assert client.access_token == "test-access-token"

    def test_client_credentials_priority_over_pat(self, monkeypatch):
        """When both are configured, client credentials takes priority."""
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", "test-client-id", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", "test-secret", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-pat-token", raising=False)

        client = UiPathClient()
        assert client.has_confidential_app() is True
        assert client.has_pat() is True
        assert client.is_configured() is True

    def test_missing_credentials_raises_error(self, monkeypatch):
        """When no credentials configured, is_configured returns False."""
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", None, raising=False)

        client = UiPathClient()
        assert client.is_configured() is False

        with pytest.raises(UiPathAuthError, match="No UiPath credentials"):
            client.get_access_token()

    def test_folder_resolution_success(self, monkeypatch):
        """Folder path resolves to folder ID and key."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {"Id": 123, "Key": "folder-key-123", "DisplayName": "Shared/Ghost-QA"},
                {"Id": 456, "Key": "folder-key-456", "DisplayName": "Other"},
            ]
        }

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            folder_id, folder_key = client.resolve_folder()
            assert folder_id == 123
            assert folder_key == "folder-key-123"

    def test_folder_resolution_not_found(self, monkeypatch):
        """Folder not found raises UiPathNotFoundError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {"Id": 123, "Key": "key-123", "DisplayName": "OtherFolder"},
            ]
        }

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            with pytest.raises(UiPathNotFoundError, match="not found"):
                client.resolve_folder()

    def test_release_resolution_success(self, monkeypatch):
        """Process name resolves to release key."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()
        client.folder_id = 123
        client.folder_key = "key-123"
        client._folder_resolved = True

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {"Key": "release-key-abc", "Name": "TestProcess", "Version": "1.0.0"},
                {"Key": "release-key-xyz", "Name": "OtherProcess", "Version": "2.0.0"},
            ]
        }

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            release_key = client.resolve_release()
            assert release_key == "release-key-abc"

    def test_release_resolution_not_found(self, monkeypatch):
        """Process not found raises UiPathNotFoundError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "NonExistent", raising=False)

        client = UiPathClient()
        client.folder_id = 123
        client.folder_key = "key-123"
        client._folder_resolved = True

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {"Key": "release-key-abc", "Name": "TestProcess", "Version": "1.0.0"},
            ]
        }

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            with pytest.raises(UiPathNotFoundError, match="not found"):
                client.resolve_release()

    def test_missing_test_process_raises_config_error(self, monkeypatch):
        """If UIPATH_TEST_PROCESS is not set, raise UiPathAPIError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", None, raising=False)

        client = UiPathClient()
        with pytest.raises(UiPathAPIError, match="UIPATH_TEST_PROCESS is not configured"):
            client.resolve_release()

    def test_start_job_success(self, monkeypatch):
        """StartJob returns a job ID."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()
        client.folder_id = 123
        client._folder_resolved = True
        client.release_key = "release-key-abc"
        client._release_resolved = True

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Id": 42, "State": "Running"}

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            job_id = client.start_job({"TestCaseId": "TC-001"})
            assert job_id == "42"

    def test_poll_job_successful(self, monkeypatch):
        """Poll returns job when state is Successful."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()
        client.folder_id = 123

        statuses = [
            {"Id": 42, "State": "Running"},
            {"Id": 42, "State": "Successful"},
        ]
        mock_responses = [MagicMock(status_code=200, json=MagicMock(return_value=s)) for s in statuses]

        with patch("app.services.uipath_client.requests.request", side_effect=mock_responses):
            with patch("time.sleep"):
                result = client.poll_job("42", timeout=10, interval=1)
                assert result["State"] == "Successful"

    def test_poll_job_timeout(self, monkeypatch):
        """Poll raises error when job doesn't complete within timeout."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()
        client.folder_id = 123

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Id": 42, "State": "Running"}

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            with patch("time.sleep"):
                with pytest.raises(UiPathAPIError, match="did not complete"):
                    client.poll_job("42", timeout=5, interval=1)

    def test_poll_job_faulted(self, monkeypatch):
        """Poll returns job when state is Faulted."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()
        client.folder_id = 123

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"Id": 42, "State": "Faulted"}

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            with patch("time.sleep"):
                result = client.poll_job("42", timeout=10, interval=1)
                assert result["State"] == "Faulted"

    def test_api_error_401_raises_auth_error(self, monkeypatch):
        """HTTP 401 raises UiPathAuthError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)

        client = UiPathClient()

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            with pytest.raises(UiPathAuthError, match="Authentication failed"):
                client._request("GET", "/orchestrator_/odata/Folders", include_folder=False)

    def test_api_error_404_raises_not_found(self, monkeypatch):
        """HTTP 404 raises UiPathNotFoundError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)

        client = UiPathClient()
        client.folder_id = 123

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            with pytest.raises(UiPathNotFoundError):
                client._request("GET", "/orchestrator_/odata/Releases")

    def test_api_error_500_raises_api_error(self, monkeypatch):
        """HTTP 500 raises UiPathAPIError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)

        client = UiPathClient()
        client.folder_id = 123

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("app.services.uipath_client.requests.request", return_value=mock_response):
            with pytest.raises(UiPathAPIError, match="HTTP 500"):
                client._request("GET", "/orchestrator_/odata/Releases")

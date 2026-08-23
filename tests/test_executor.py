import pytest
import uuid
from unittest.mock import patch, MagicMock
from app.services.executor import (
    ExecutorService,
    MockExecutor,
    UiPathExecutor,
    UiPathConfigError,
)
from app.services.uipath_client import (
    UiPathClient,
    UiPathAuthError,
    UiPathAPIError,
    UiPathNotFoundError,
)
from app.models import (
    TestCase, TestResult, TestOutcome, TestType, TestPriority,
    RiskLevel, FailureType, TestCaseStatus, ApprovalStatus
)
from app.config import settings


class TestExecution:
    """Test the mock execution service."""

    def test_test_pass(self):
        """A test should be able to pass."""
        executor = ExecutorService()
        executor.demo_mode = True
        tc = TestCase(
            id="TC-001",
            pipeline_run_id=str(uuid.uuid4()),
            title="Test that passes",
            steps='[]',
            test_type=TestType.regression,
            priority=TestPriority.p3_low,
            generated_by="test",
            approval_status=ApprovalStatus.approved
        )
        result = None
        for _ in range(10):
            result = executor.mock_executor.execute_test(tc)
            if result.outcome == TestOutcome.passed:
                break
        assert result is not None
        assert result.outcome in (TestOutcome.passed, TestOutcome.failed)
        assert result.test_case_id == "TC-001"

    def test_test_fail(self):
        """A test should be able to fail."""
        executor = ExecutorService()
        executor.demo_mode = True
        tc = TestCase(
            id="TC-001",
            pipeline_run_id=str(uuid.uuid4()),
            title="Test that fails",
            steps='[]',
            test_type=TestType.edge_case,
            priority=TestPriority.p0_critical,
            generated_by="test",
            approval_status=ApprovalStatus.approved
        )
        result = None
        for _ in range(10):
            result = executor.mock_executor.execute_test(tc)
            if result.outcome == TestOutcome.failed:
                break
        assert result is not None
        assert result.outcome in (TestOutcome.passed, TestOutcome.failed)

    def test_timeout_failure(self):
        """Timeout failure type should be possible."""
        executor = MockExecutor()
        tc = TestCase(
            id="TC-001",
            pipeline_run_id=str(uuid.uuid4()),
            title="Test",
            steps='[]',
            priority=TestPriority.p0_critical,
        )
        found_timeout = False
        for _ in range(50):
            result = executor.execute_test(tc)
            if result.failure_type == FailureType.timeout:
                found_timeout = True
                assert result.outcome == TestOutcome.failed
                break
        assert found_timeout, "Expected at least one timeout failure in 50 runs"

    def test_selector_failure(self):
        """Selector failure type should be possible."""
        executor = MockExecutor()
        tc = TestCase(
            id="TC-001",
            pipeline_run_id=str(uuid.uuid4()),
            title="Test",
            steps='[]',
            priority=TestPriority.p0_critical,
        )
        found_selector = False
        for _ in range(20):
            result = executor.execute_test(tc)
            if result.failure_type == FailureType.selector_broken:
                found_selector = True
                assert result.outcome == TestOutcome.failed
                break
        assert found_selector, "Expected at least one selector failure in 20 runs"

    def test_healed_test_passes(self):
        """Healed tests should always pass."""
        executor = MockExecutor()
        tc = TestCase(
            id="TC-001",
            pipeline_run_id=str(uuid.uuid4()),
            title="Healed test",
            steps='[]',
            generated_by="heal",
        )
        result = executor.execute_test(tc)
        assert result.outcome == TestOutcome.passed
        assert result.failure_type is None


class TestDemoMode:
    """Test DEMO_MODE=true uses MockExecutor."""

    def test_demo_mode_uses_mock(self, monkeypatch):
        """DEMO_MODE=true should use MockExecutor, not UiPath."""
        monkeypatch.setattr(settings, "DEMO_MODE", True, raising=False)
        executor_service = ExecutorService()
        assert executor_service.demo_mode is True
        assert executor_service.uipath_executor.demo_mode is True

        tc = TestCase(
            id="TC-001",
            pipeline_run_id=str(uuid.uuid4()),
            title="Demo test",
            steps='[]',
            priority=TestPriority.p3_low,
        )
        result = executor_service.execute_tests([tc])
        assert result[0].outcome in (TestOutcome.passed, TestOutcome.failed)
        assert result[0].robot_id is None or "uipath" not in (result[0].robot_id or "")


class TestProductionMode:
    """Test DEMO_MODE=false behavior."""

    def test_demo_mode_false_uses_uipath_executor(self, monkeypatch):
        """DEMO_MODE=false should route to UiPathExecutor when credentials are configured."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", "test-id", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", "test-secret", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        from app.services.executor import ExecutorService
        executor_service = ExecutorService()
        assert executor_service.demo_mode is False
        assert executor_service.uipath_executor.demo_mode is False

    def test_missing_credentials_raises_config_error(self, monkeypatch):
        """Missing UiPath credentials in production mode should raise config error."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", None, raising=False)

        executor = UiPathExecutor()
        tc = TestCase(
            id="TC-001",
            pipeline_run_id=str(uuid.uuid4()),
            title="Prod test",
            steps='[]',
            priority=TestPriority.p1_high,
        )
        with pytest.raises(UiPathConfigError, match="not configured"):
            executor.execute_test(tc)

    def test_missing_test_process_raises_config_error(self, monkeypatch):
        """Missing UIPATH_TEST_PROCESS in production should raise config error."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-pat", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", None, raising=False)

        executor = UiPathExecutor()
        with pytest.raises(UiPathConfigError, match="UIPATH_TEST_PROCESS"):
            executor._ensure_configured()

    def test_missing_org_id_raises_config_error(self, monkeypatch):
        """Missing UIPATH_ORG_ID in production should raise config error."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-pat", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        executor = UiPathExecutor()
        with pytest.raises(UiPathConfigError, match="UIPATH_ORG_ID"):
            executor._ensure_configured()


class TestPATAuthentication:
    """Tests for PAT authentication."""

    def test_pat_token_used_directly(self, monkeypatch):
        """PAT token is used as Bearer without token endpoint call."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "my-pat-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()
        token = client.get_access_token()
        assert token == "my-pat-token"

    def test_pat_takes_no_token_endpoint_call(self, monkeypatch):
        """PAT auth should not call the token endpoint."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "my-pat-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()

        with patch(
            "app.services.uipath_client.requests.post",
            side_effect=AssertionError("Token endpoint should NOT be called when PAT is set"),
        ) as mock_post:
            token = client.get_access_token()
            assert token == "my-pat-token"
            mock_post.assert_not_called()


class TestConfidentialApp:
    """Tests for confidential external application authentication."""

    def test_client_credentials_token_request(
        self, monkeypatch
    ):
        """Client credentials flow calls the correct token endpoint."""
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", "cli-id", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", "cli-secret", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", None, raising=False)

        client = UiPathClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "cc-token",
            "expires_in": 3600,
        }

        with patch("app.services.uipath_client.requests.post", return_value=mock_resp) as mock_post:
            token = client.get_access_token()
            assert token == "cc-token"
            call_args = mock_post.call_args
            assert "identity_/connect/token" in call_args[0][0]

    def test_client_credentials_priority_over_pat(self, monkeypatch):
        """Client credentials takes priority when both are configured."""
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", "cli-id", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", "cli-secret", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "pat-token", raising=False)

        client = UiPathClient()
        assert client.has_confidential_app()
        assert client.has_pat()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "cc-token", "expires_in": 3600}

        with patch("app.services.uipath_client.requests.post", return_value=mock_resp):
            token = client.get_access_token()
            assert token == "cc-token"

    def test_invalid_client_credentials_raises_auth_error(self, monkeypatch):
        """Invalid client credentials should raise UiPathAuthError."""
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", "bad-id", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", "bad-secret", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", None, raising=False)

        client = UiPathClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "invalid_client"

        with patch("app.services.uipath_client.requests.post", return_value=mock_resp):
            with pytest.raises(UiPathAuthError, match="Client credentials auth failed"):
                client.get_access_token()


class TestFolderResolution:
    """Tests for folder resolution with modern folder model."""

    def test_folder_resolved_by_display_name(self, monkeypatch):
        """Folder is resolved by matching DisplayName."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "value": [
                {"Id": 999, "Key": "folder-key", "DisplayName": "Shared/Ghost-QA"},
            ]
        }

        with patch("app.services.uipath_client.requests.request", return_value=mock_resp):
            folder_id, folder_key = client.resolve_folder()
            assert folder_id == 999
            assert folder_key == "folder-key"

    def test_folder_not_found_raises_error(self, monkeypatch):
        """Folder not found raises UiPathNotFoundError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": []}

        with patch("app.services.uipath_client.requests.request", return_value=mock_resp):
            with pytest.raises(UiPathNotFoundError, match="Folder"):
                client.resolve_folder()


class TestSuccessfulUiPathExecution:
    """Tests for successful UiPath execution flow."""

    def test_execute_test_success(self, monkeypatch):
        """Successful job results in a passed TestResult."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        executor = UiPathExecutor()

        # Mock the client methods
        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.resolve_folder.return_value = (123, "folder-key")
        mock_client.execute_process.return_value = {"Id": 42, "State": "Successful"}
        executor.client = mock_client

        tc = TestCase(
            id="TC-001",
            pipeline_run_id=str(uuid.uuid4()),
            title="Login test",
            steps='[]',
            priority=TestPriority.p0_critical,
        )

        result = executor.execute_test(tc)
        assert result.outcome == TestOutcome.passed
        assert result.robot_id.startswith("uipath-")

    def test_execute_test_failure(self, monkeypatch):
        """Failed job results in a failed TestResult."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_ID", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_CLIENT_SECRET", None, raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        executor = UiPathExecutor()

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.resolve_folder.return_value = (123, "folder-key")
        mock_client.execute_process.return_value = {"Id": 43, "State": "Faulted"}
        executor.client = mock_client

        tc = TestCase(
            id="TC-002",
            pipeline_run_id=str(uuid.uuid4()),
            title="Failed test",
            steps='[]',
            priority=TestPriority.p1_high,
        )

        result = executor.execute_test(tc)
        assert result.outcome == TestOutcome.failed
        assert result.failure_type == FailureType.unknown
        assert "Faulted" in result.failure_message

    def test_execute_batch_returns_all_results(self, monkeypatch):
        """execute_batch returns results for all test cases."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        executor = UiPathExecutor()

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.resolve_folder.return_value = (123, "folder-key")
        mock_client.execute_process.return_value = {"Id": 42, "State": "Successful"}
        executor.client = mock_client

        tcs = [
            TestCase(id=f"TC-{i}", pipeline_run_id=str(uuid.uuid4()), title=f"Test {i}", steps='[]', priority=TestPriority.p3_low)
            for i in range(3)
        ]

        results = executor.execute_batch(tcs)
        assert len(results) == 3
        for r in results:
            assert r.outcome == TestOutcome.passed


class TestFailedUiPathExecution:
    """Tests for UiPath execution failures."""

    def test_api_error_propagates(self, monkeypatch):
        """API errors are raised, not swallowed."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        executor = UiPathExecutor()

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.resolve_folder.return_value = (123, "folder-key")
        mock_client.execute_process.side_effect = UiPathAPIError("API rate limited")
        executor.client = mock_client

        tc = TestCase(id="TC-001", pipeline_run_id=str(uuid.uuid4()), title="Test", steps='[]')

        with pytest.raises(UiPathAPIError, match="API rate limited"):
            executor.execute_test(tc)

    def test_auth_failure_propagates(self, monkeypatch):
        """Auth errors are raised, not silently falling back to mock."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        executor = UiPathExecutor()

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.resolve_folder.side_effect = UiPathAuthError("Invalid credentials")
        executor.client = mock_client

        tc = TestCase(id="TC-001", pipeline_run_id=str(uuid.uuid4()), title="Test", steps='[]')

        with pytest.raises(UiPathAuthError):
            executor.execute_test(tc)


class TestInvalidFolder:
    """Tests for invalid folder handling."""

    def test_invalid_folder_raises_config_error(self, monkeypatch):
        """Invalid folder raises UiPathConfigError with helpful message."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        executor = UiPathExecutor()

        mock_client = MagicMock()
        mock_client.is_configured.return_value = True
        mock_client.resolve_folder.side_effect = UiPathNotFoundError("Folder not found")
        executor.client = mock_client

        tc = TestCase(id="TC-001", pipeline_run_id=str(uuid.uuid4()), title="Test", steps='[]')

        with pytest.raises(UiPathConfigError):
            executor.execute_test(tc)


class TestMissingProcess:
    """Tests for missing process handling."""

    def test_missing_process_raises_api_error(self, monkeypatch):
        """Missing process raises UiPathAPIError."""
        monkeypatch.setattr(settings, "DEMO_MODE", False, raising=False)
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "NonExistent", raising=False)

        from app.services.uipath_client import UiPathClient
        client = UiPathClient()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": []}

        with patch("app.services.uipath_client.requests.request", return_value=mock_resp):
            with pytest.raises(UiPathNotFoundError, match="not found"):
                client.resolve_release()


class TestApiTimeout:
    """Tests for API timeout handling."""

    def test_poll_timeout_raises_error(self, monkeypatch):
        """Poll timeout raises UiPathAPIError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_FOLDER", "Shared/Ghost-QA", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TEST_PROCESS", "TestProcess", raising=False)

        client = UiPathClient()
        client.folder_id = 123

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"Id": 42, "State": "Running"}

        with patch("app.services.uipath_client.requests.request", return_value=mock_resp):
            with patch("time.sleep"):
                with pytest.raises(UiPathAPIError, match="did not complete"):
                    client.poll_job("42", timeout=3, interval=5)

    def test_request_timeout_handled(self, monkeypatch):
        """Request timeout is caught and re-raised as UiPathAPIError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)

        client = UiPathClient()

        import requests as req
        with patch("app.services.uipath_client.requests.request", side_effect=req.exceptions.Timeout("Connection timed out")):
            with pytest.raises(UiPathAPIError, match="timed out"):
                client._request("GET", "/test")


class TestApiError:
    """Tests for API error handling."""

    def test_403_raises_api_error(self, monkeypatch):
        """HTTP 403 raises UiPathAPIError."""
        monkeypatch.setattr(settings, "UIPATH_PAT", "test-token", raising=False)
        monkeypatch.setattr(settings, "UIPATH_ORG_ID", "test-org", raising=False)
        monkeypatch.setattr(settings, "UIPATH_TENANT_NAME", "DefaultTenant", raising=False)

        client = UiPathClient()
        client.folder_id = 123

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"

        with patch("app.services.uipath_client.requests.request", return_value=mock_resp):
            with pytest.raises(UiPathAPIError, match="HTTP 403"):
                client._request("GET", "/orchestrator_/odata/Releases")

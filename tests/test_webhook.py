import json
import hashlib
import hmac
import pytest
from app.services.github import GitHubService
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    from app.database import init_db, SessionLocal
    init_db()
    
    # Clear all data from tables
    from app.database import Base
    session = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()
    
    with TestClient(app) as c:
        yield c


def _make_webhook_payload(action="opened", pr_number=42):
    return {
        "action": action,
        "repository": {
            "id": 123456789,
            "name": "demo-app",
            "full_name": "demo-org/demo-app",
            "owner": {"login": "demo-org"},
            "default_branch": "main"
        },
        "pull_request": {
            "number": pr_number,
            "title": "Add login endpoint",
            "body": "Implements JWT login. Fixes #41",
            "state": "open",
            "diff_url": "https://github.com/demo-org/demo-app/pull/42.diff",
            "html_url": "https://github.com/demo-org/demo-app/pull/42",
            "user": {"login": "developer"},
            "head": {"ref": "feature/login", "sha": "abc123"},
            "base": {"ref": "main", "sha": "def456"},
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z"
        }
    }


class TestWebhookSignature:
    def test_valid_signature(self):
        """Valid webhook signature should pass verification."""
        github = GitHubService()
        github.webhook_secret = "test-secret"
        payload = b'{"test": "data"}'
        signature = hmac.new(
            b"test-secret", payload, hashlib.sha256
        ).hexdigest()
        header = f"sha256={signature}"
        assert github.verify_signature(payload, header) is True

    def test_invalid_signature(self):
        """Invalid webhook signature should fail verification."""
        github = GitHubService()
        github.webhook_secret = "test-secret"
        payload = b'{"test": "data"}'
        header = "sha256=invalid_signature"
        assert github.verify_signature(payload, header) is False

    def test_no_secret_skips_verification(self):
        """When no secret is configured, verification should be skipped."""
        github = GitHubService()
        github.webhook_secret = None
        assert github.verify_signature(b"payload", "") is True

    def test_wrong_algorithm(self):
        """Non-sha256 algorithm should fail."""
        github = GitHubService()
        github.webhook_secret = "test-secret"
        payload = b'{"test": "data"}'
        header = "md5=abc123"
        assert github.verify_signature(payload, header) is False


class TestPRExtraction:
    def test_extract_pr_info(self):
        """PR info should be correctly extracted from payload."""
        github = GitHubService()
        payload = {
            "action": "opened",
            "repository": {
                "id": 12345,
                "name": "demo-app",
                "full_name": "demo-org/demo-app",
                "owner": {"login": "demo-org"},
                "default_branch": "main"
            },
            "pull_request": {
                "number": 42,
                "title": "Add login endpoint",
                "body": "Implements JWT login",
                "state": "open",
                "diff_url": "https://github.com/demo-org/demo-app/pull/42.diff",
                "html_url": "https://github.com/demo-org/demo-app/pull/42",
                "user": {"login": "developer"},
                "head": {"ref": "feature/login", "sha": "abc123"},
                "base": {"ref": "main", "sha": "def456"},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }
        }
        info = github.extract_pr_info(payload)
        assert info["repo_full_name"] == "demo-org/demo-app"
        assert info["repo_owner"] == "demo-org"
        assert info["repo_name"] == "demo-app"
        assert info["pr_number"] == 42
        assert info["pr_title"] == "Add login endpoint"
        assert info["commit_sha"] == "abc123"
        assert info["branch"] == "feature/login"


class TestWebhookHTTP:
    """Test webhook endpoint via HTTP."""

    def test_valid_webhook(self, client):
        """Valid webhook should start a pipeline."""
        payload = _make_webhook_payload(pr_number=100)
        response = client.post(
            "/api/webhooks/github",
            json=payload,
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=test"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pipeline_started"
        assert data["pr_number"] == 100

    def test_invalid_signature(self, client):
        """Invalid signature should return 401 when secret is configured."""
        from app.api.webhooks import github_service
        original = github_service.webhook_secret
        github_service.webhook_secret = "real-secret"

        payload = _make_webhook_payload()
        response = client.post(
            "/api/webhooks/github",
            json=payload,
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=invalid"}
        )
        assert response.status_code == 401

        github_service.webhook_secret = original

    def test_unsupported_event(self, client):
        """Push events should be ignored."""
        response = client.post(
            "/api/webhooks/github",
            json={"ref": "refs/heads/main"},
            headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=test"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    def test_ignored_action(self, client):
        """Closed PR events should be ignored."""
        response = client.post(
            "/api/webhooks/github",
            json=_make_webhook_payload(action="closed"),
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=test"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    def test_duplicate_webhook(self, client):
        """Duplicate webhook with same PR and SHA should be ignored."""
        payload = _make_webhook_payload(pr_number=99)
        response1 = client.post(
            "/api/webhooks/github",
            json=payload,
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=test"}
        )
        assert response1.status_code == 200
        data1 = response1.json()
        assert data1["status"] == "pipeline_started"

        response2 = client.post(
            "/api/webhooks/github",
            json=payload,
            headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=test"}
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["status"] == "duplicate_ignored"
        assert data2["pipeline_run_id"] == data1["pipeline_run_id"]

import logging
import requests
from typing import List, Optional, Dict, Any
from app.config import settings
from app.models import PipelineRun, TestCase

logger = logging.getLogger(__name__)


class ActionCenterService:
    def __init__(self):
        self.base_url = settings.UIPATH_ACTION_CENTER_BASE
        self.enabled = settings.DEMO_MODE is False and bool(settings.UIPATH_CLIENT_ID)

    def create_task(
        self,
        pipeline_run: PipelineRun,
        test_cases: List[TestCase]
    ) -> Optional[str]:
        """
        Create an Action Center task; return task_id.
        On failure: log at ERROR, return None to signal fallback to local approval.
        """
        if not self.enabled:
            return None

        # Build task payload
        tests_data = [
            {"id": tc.id, "title": tc.title, "risk": tc.risk_level.value if tc.risk_level else "medium"}
            for tc in test_cases
        ]
        payload = {
            "Title": f"Ghost QA: Approve tests for PR #{pipeline_run.github_pr_number}",
            "Priority": "Medium",
            "Data": {
                "pipelineRunId": pipeline_run.id,
                "tests": tests_data
            },
            "ActionCatalogName": "GhostQA_Approval"
        }

        try:
            token = self._get_access_token()
            url = f"{self.base_url}/identity_api/v1/actions"
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=30
            )
            resp.raise_for_status()
            task_id = resp.json().get("Id")
            return task_id
        except Exception as e:
            logger.error(f"Action Center task creation failed: {e}")
            return None

    def cancel_task(self, task_id: str) -> None:
        """Cancel a pending Action Center task."""
        if not self.enabled:
            return

        try:
            token = self._get_access_token()
            url = f"{self.base_url}/identity_api/v1/actions/{task_id}/cancel"
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Action Center task cancellation failed: {e}")

    def poll_task(self, task_id: str) -> Dict[str, Any]:
        """Return task status and approved/rejected test IDs."""
        if not self.enabled:
            return {"Status": "Unknown"}

        try:
            token = self._get_access_token()
            url = f"{self.base_url}/identity_api/v1/actions/{task_id}"
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "Status": data.get("Status"),
                "ApprovedTestIds": data.get("Data", {}).get("ApprovedTestIds", []),
                "RejectedTestIds": data.get("Data", {}).get("RejectedTestIds", [])
            }
        except Exception as e:
            logger.error(f"Action Center task polling failed: {e}")
            return {"Status": "Error"}

    def _get_access_token(self) -> str:
        """Authenticate with UiPath using client credentials."""
        from app.services.executor import UiPathExecutor
        executor = UiPathExecutor()
        return executor._get_access_token()

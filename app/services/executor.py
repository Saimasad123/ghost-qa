import json
import random
import logging
import time
import uuid
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.config import settings
from app.models import TestCase, TestResult, TestOutcome, FailureType, TestCaseStatus
from app.database import SessionLocal
from app.schemas.test_schemas import TestResultSchema
from app.services.uipath_client import (
    UiPathClient,
    UiPathAuthError,
    UiPathAPIError,
    UiPathNotFoundError,
)

logger = logging.getLogger(__name__)


class UiPathConfigError(Exception):
    """Raised when UiPath is required (DEMO_MODE=false) but not properly configured."""
    pass


class MockExecutor:
    """Mock executor for demo mode when UiPath is not available."""

    def execute_test(self, test_case: TestCase) -> TestResult:
        steps = json.loads(test_case.steps) if test_case.steps else []
        duration = random.randint(500, 5000)

        if getattr(test_case, 'generated_by', '') == 'heal':
            return TestResult(
                id=str(uuid.uuid4()),
                test_case_id=test_case.id,
                outcome=TestOutcome.passed,
                duration_ms=duration,
                executed_at=datetime.utcnow()
            )

        fail_probability = 0.3
        if test_case.priority.value in ("p0_critical", "p1_high"):
            fail_probability = 0.4
        test_type_val = test_case.test_type.value if test_case.test_type else ""
        if test_type_val in ("edge_case", "integration"):
            fail_probability = 0.6

        if random.random() < fail_probability:
            failure_types = [FailureType.selector_broken, FailureType.api_contract, FailureType.assertion_stale, FailureType.timeout]
            weights = [0.4, 0.2, 0.2, 0.2]
            failure_type = random.choices(failure_types, weights=weights, k=1)[0]

            failure_messages = {
                FailureType.selector_broken: "Element not found: selector could not be located on the page",
                FailureType.assertion_failed: "Expected result did not match actual result",
                FailureType.timeout: "Operation timed out after 30 seconds",
                FailureType.api_contract: "API response did not match expected schema",
                FailureType.unknown: "Unknown test failure occurred"
            }

            failure_steps = {
                FailureType.selector_broken: "step_3" if len(steps) > 3 else "step_1",
                FailureType.assertion_failed: steps[-1].get("action", "last_step") if steps else "final_step",
                FailureType.timeout: "step_2" if len(steps) > 2 else "step_1",
                FailureType.api_contract: "step_1" if len(steps) > 1 else "step_1",
                FailureType.unknown: "unknown"
            }

            return TestResult(
                id=str(uuid.uuid4()),
                test_case_id=test_case.id,
                outcome=TestOutcome.failed,
                failure_step=failure_steps.get(failure_type, "unknown"),
                failure_message=failure_messages.get(failure_type, "Test failed"),
                failure_type=failure_type,
                duration_ms=duration,
                executed_at=datetime.utcnow()
            )
        else:
            return TestResult(
                id=str(uuid.uuid4()),
                test_case_id=test_case.id,
                outcome=TestOutcome.passed,
                duration_ms=duration,
                executed_at=datetime.utcnow()
            )

    def execute_batch(self, test_cases: List[TestCase]) -> List[TestResult]:
        results = []
        for test_case in test_cases:
            time.sleep(0.1)
            result = self.execute_test(test_case)
            results.append(result)
        return results


class UiPathExecutor:
    """
    Real UiPath executor using UiPath Automation Cloud APIs.

    Authentication priority:
    1. Confidential external application (client credentials) — preferred for production
    2. Personal Access Token (PAT) — for development/testing

    Uses modern UiPath folder model (Shared/Ghost-QA) — NOT legacy environments.
    """

    def __init__(self):
        self.client_id = settings.UIPATH_CLIENT_ID
        self.client_secret = settings.UIPATH_CLIENT_SECRET
        self.tenant_name = settings.UIPATH_TENANT_NAME
        self.org_id = settings.UIPATH_ORG_ID
        self.folder_path = settings.UIPATH_TEST_FOLDER
        self.test_process = settings.UIPATH_TEST_PROCESS
        self.pat = settings.UIPATH_PAT
        self.demo_mode = settings.DEMO_MODE
        self.auto_approve = settings.AUTO_APPROVE

        # Build the UiPath client — it handles auth and API calls
        self.client = UiPathClient()
        self.access_token = None
        self.token_expires_at = 0
        self.folder_id = None
        self.folder_key = None

    def _ensure_configured(self) -> None:
        """Raise UiPathConfigError if required config is missing in production mode."""
        if self.demo_mode:
            return
        if not self.client.is_configured():
            raise UiPathConfigError(
                "UiPath credentials not configured. "
                "Set UIPATH_PAT or UIPATH_CLIENT_ID + UIPATH_CLIENT_SECRET + UIPATH_TENANT_NAME. "
                "Or set DEMO_MODE=true for mock execution."
            )
        if not self.org_id:
            raise UiPathConfigError("UIPATH_ORG_ID is required for UiPath integration.")
        if not self.test_process:
            raise UiPathConfigError(
                "UIPATH_TEST_PROCESS is required in production mode. "
                "Set it to the name of your UiPath process/package."
            )

    def _resolve_folder(self) -> None:
        """Resolve the configured folder path to its ID and key."""
        try:
            self.folder_id, self.folder_key = self.client.resolve_folder()
        except UiPathAuthError:
            raise
        except UiPathNotFoundError as e:
            logger.error(f"Folder resolution failed: {e}")
            raise UiPathConfigError(
                f"UiPath folder '{self.folder_path}' not found in org '{self.org_id}'. "
                f"Create this folder in UiPath Automation Cloud "
                f"(Organize -> Folders -> New Folder, path: {self.folder_path})"
            )

    def execute_test(self, test_case: TestCase) -> TestResult:
        """
        Execute a single test case through UiPath.

        In DEMO_MODE, uses MockExecutor.
        In production mode, calls real UiPath APIs.
        """
        if self.demo_mode:
            mock = MockExecutor()
            return mock.execute_test(test_case)

        # Production mode — real UiPath execution
        self._ensure_configured()
        self._resolve_folder()

        test_case_dict = {
            "id": test_case.id,
            "title": test_case.title,
            "type": test_case.test_type.value if test_case.test_type else "functional",
            "priority": test_case.priority.value if test_case.priority else "p2_medium",
            "steps": json.loads(test_case.steps) if test_case.steps else [],
            "expected_result": test_case.expected_result or "",
            "risk_level": test_case.risk_level.value if test_case.risk_level else "medium",
        }

        input_args = {
            "TestCaseId": test_case.id,
            "TestCaseTitle": test_case.title,
            "TestSteps": json.dumps(test_case_dict["steps"]),
            "ExpectedResult": test_case_dict["expected_result"],
        }

        start_time = time.time()
        try:
            logger.info(f"Triggering UiPath execution for test {test_case.id}")
            job_result = self.client.execute_process(
                input_arguments=input_args,
                timeout=120,
            )

            duration_ms = int((time.time() - start_time) * 1000)
            state_raw = (job_result.get("State") or job_result.get("state") or "")
            state = state_raw.lower()

            if state in ("successful", "success"):
                job_id_str = str(job_result.get("Id", ""))
                return TestResult(
                    id=str(uuid.uuid4()),
                    test_case_id=test_case.id,
                    outcome=TestOutcome.passed,
                    duration_ms=duration_ms,
                    robot_id=f"uipath-{job_id_str[:8]}",
                    executed_at=datetime.utcnow()
                )
            else:
                job_id_str = str(job_result.get("Id", "?"))
                return TestResult(
                    id=str(uuid.uuid4()),
                    test_case_id=test_case.id,
                    outcome=TestOutcome.failed,
                    failure_step="job_execution",
                    failure_message=f"UiPath job {job_id_str} ended with state: {state_raw or 'unknown'}",
                    failure_type=FailureType.unknown,
                    duration_ms=duration_ms,
                    robot_id=f"uipath-{job_id_str[:8]}",
                    executed_at=datetime.utcnow()
                )

        except UiPathAuthError as e:
            logger.error(f"UiPath authentication failed: {e}")
            raise
        except UiPathAPIError as e:
            logger.error(f"UiPath API error for test {test_case.id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error executing test {test_case.id}: {e}")
            raise UiPathAPIError(f"UiPath execution failed: {e}")

    def execute_batch(self, test_cases: List[TestCase]) -> List[TestResult]:
        results = []
        for test_case in test_cases:
            result = self.execute_test(test_case)
            results.append(result)
        return results


class ExecutorService:
    def __init__(self):
        self.mock_executor = MockExecutor()
        self.uipath_executor = UiPathExecutor()
        self.demo_mode = settings.DEMO_MODE or not all([
            settings.UIPATH_CLIENT_ID or settings.UIPATH_PAT,
            settings.UIPATH_TENANT_NAME,
            settings.UIPATH_ORG_ID,
        ])

    def execute_tests(self, test_cases: List[TestCase]) -> List[TestResult]:
        if self.demo_mode:
            executor = self.mock_executor
        else:
            executor = self.uipath_executor
        return executor.execute_batch(test_cases)

    def store_results(self, results: List[TestResult], heal_attempt_id: Optional[str] = None) -> None:
        db = SessionLocal()
        try:
            for result in results:
                stored = TestResult(
                    id=result.id,
                    test_case_id=result.test_case_id,
                    outcome=result.outcome,
                    failure_step=result.failure_step,
                    failure_message=result.failure_message,
                    failure_type=result.failure_type,
                    screenshot_url=result.screenshot_url,
                    duration_ms=result.duration_ms,
                    robot_id=result.robot_id,
                    heal_attempt_id=heal_attempt_id,
                    executed_at=result.executed_at
                )
                db.add(stored)
                test_case = db.query(TestCase).filter(TestCase.id == result.test_case_id).first()
                if test_case:
                    test_case.outcome = result.outcome
                    test_case.failure_step = result.failure_step
                    test_case.failure_message = result.failure_message
                    test_case.failure_type = result.failure_type
                    test_case.duration_ms = result.duration_ms
                    test_case.executed_at = result.executed_at
                    test_case.status = TestCaseStatus.passed if result.outcome == TestOutcome.passed else TestCaseStatus.failed
            db.commit()
        finally:
            db.close()

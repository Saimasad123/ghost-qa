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

logger = logging.getLogger(__name__)


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
    """Real UiPath executor for production with Test Cloud integration."""

    def __init__(self):
        self.client_id = settings.UIPATH_CLIENT_ID
        self.client_secret = settings.UIPATH_CLIENT_SECRET
        self.tenant_name = settings.UIPATH_TENANT_NAME
        self.org_id = settings.UIPATH_ORG_ID
        self.environment_id = settings.UIPATH_ENVIRONMENT_ID
        self.test_folder = settings.UIPATH_TEST_FOLDER
        self.demo_mode = settings.DEMO_MODE or not all([
            self.client_id, self.client_secret, self.tenant_name,
            self.org_id, self.environment_id
        ])
        self.access_token = None
        self.token_expires_at = 0

    def _get_access_token(self) -> str:
        """Authenticate with UiPath using client credentials."""
        if self.demo_mode or not self.client_id:
            return "mock-token"
        if self.access_token and time.time() < self.token_expires_at:
            return self.access_token

        try:
            resp = requests.post(
                "https://cloud.uipath.com/identity/connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "OR.AuthAPI"
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
                allow_redirects=False
            )
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data.get("access_token")
                self.token_expires_at = time.time() + data.get("expires_in", 3600) - 60
                logger.info("Successfully authenticated with UiPath")
                return self.access_token
            else:
                logger.warning(f"UiPath auth returned {resp.status_code}: {resp.text[:200]}")
                return "mock-token"
        except Exception as e:
            logger.error(f"UiPath authentication failed: {e}")
            return "mock-token"

    def discover_organizations(self) -> List[Dict[str, Any]]:
        """Discover available organizations in UiPath Cloud."""
        token = self._get_access_token()
        if token == "mock-token":
            return []
        try:
            resp = requests.get(
                "https://cloud.uipath.com/identity_api/v1/organizations",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("items", [])
        except Exception as e:
            logger.error(f"Failed to discover organizations: {e}")
            return []

    def discover_environments(self) -> List[Dict[str, Any]]:
        """Discover available environments in UiPath Orchestrator."""
        token = self._get_access_token()
        if token == "mock-token" or not self.org_id:
            return []
        base_url = f"https://cloud.uipath.com/{self.org_id}/{self.tenant_name}"
        try:
            resp = requests.get(
                f"{base_url}/orchestrator_/odata/ProcessTypes",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("value", [])
        except Exception as e:
            logger.error(f"Failed to discover environments: {e}")
            return []

    def execute_test(self, test_case: TestCase) -> TestResult:
        # Fallback logic: DEMO_MODE or missing credentials → silent MockExecutor
        if self.demo_mode:
            mock = MockExecutor()
            return mock.execute_test(test_case)

        token = self._get_access_token()
        if token == "mock-token":
            mock = MockExecutor()
            return mock.execute_test(test_case)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        base_url = f"{settings.UIPATH_TEST_MANAGER_BASE}/{self.org_id}/{self.tenant_name}"

        try:
            # Step 1: Generate XAML (already authenticated via _get_access_token)
            from app.services.xaml_generator import XamlGenerator
            xaml_gen = XamlGenerator()
            test_case_dict = {
                "id": test_case.id,
                "title": test_case.title,
                "type": test_case.test_type.value,
                "priority": test_case.priority.value,
                "steps": json.loads(test_case.steps) if test_case.steps else [],
                "expected_result": test_case.expected_result,
                "risk_level": test_case.risk_level.value if test_case.risk_level else "medium"
            }
            xaml_content = xaml_gen.generate_xaml(test_case_dict)

            # Step 2: Upload XAML to Test Manager
            upload_resp = requests.post(
                f"{base_url}/testmanager_/api/v1/testcases",
                headers=headers,
                files={
                    "file": ("test.xaml", xaml_content.encode("utf-8"), "application/xml"),
                    "name": (None, test_case.title, "text/plain")
                },
                timeout=30
            )
            upload_resp.raise_for_status()
            upload_data = upload_resp.json()
            uipath_test_id = upload_data.get("Id") or upload_data.get("id")
            if not uipath_test_id:
                raise ValueError("Failed to extract uipath_test_id from upload response")
            
            # Store uipath_test_id on the TestCase record
            db = SessionLocal()
            try:
                db.execute(
                    "UPDATE test_cases SET uipath_test_id = :uipath_test_id WHERE id = :test_case_id"
                ), {"uipath_test_id": uipath_test_id, "test_case_id": test_case.id}
                db.commit()
            finally:
                db.close()

            # Step 3: Create test set
            run_id_8 = test_case.pipeline_run_id[:8] if test_case.pipeline_run_id else str(uuid.uuid4())[:8]
            create_testset_resp = requests.post(
                f"{base_url}/testmanager_/api/v1/testsets",
                headers=headers,
                json={
                    "Name": f"GhostQA-{run_id_8}",
                    "TestCases": [{"TestCaseId": uipath_test_id}]
                },
                timeout=30
            )
            create_testset_resp.raise_for_status()
            testset_data = create_testset_resp.json()
            test_set_id = testset_data.get("Id") or testset_data.get("id")
            if not test_set_id:
                raise ValueError("Failed to extract test_set_id from testset creation response")

            # Step 4: Trigger execution
            trigger_resp = requests.post(
                f"{base_url}/testmanager_/api/v1/testsets/{test_set_id}/start",
                headers=headers,
                json={"EnvironmentId": self.environment_id},
                timeout=30
            )
            trigger_resp.raise_for_status()
            trigger_data = trigger_resp.json()
            test_set_execution_id = trigger_data.get("TestSetExecutionId") or trigger_data.get("id")
            if not test_set_execution_id:
                raise ValueError("Failed to extract test_set_execution_id from trigger response")

            # Step 5: Poll for completion
            timeout_at = time.time() + settings.UIPATH_EXECUTION_TIMEOUT_SECONDS
            poll_interval = 10
            
            while time.time() < timeout_at:
                poll_resp = requests.get(
                    f"{base_url}/testmanager_/api/v1/testsetexecutions/{test_set_execution_id}",
                    headers=headers,
                    timeout=30
                )
                poll_resp.raise_for_status()
                poll_data = poll_resp.json()
                
                status = (poll_data.get("Status") or poll_data.get("status") or "").lower()
                result_payload = poll_data.get("Result") or poll_data.get("result") or {}
                
                # Terminal states: Passed, Failed, Cancelled, TimedOut
                if status in ("passed", "failed", "cancelled", "timedout", "timeout"):
                    # Extract screenshot URL
                    screenshot_url = (poll_data.get("ScreenshotUrl") or 
                                     poll_data.get("screenshot_url") or 
                                     result_payload.get("ScreenshotUrl") or 
                                     result_payload.get("screenshot_url"))
                    
                    # Map result
                    if status == "passed":
                        outcome = TestOutcome.passed
                        failure_type = None
                        failure_message = None
                    elif status in ("failed", "cancelled"):
                        outcome = TestOutcome.failed
                        failure_type = (result_payload.get("FailureType") or 
                                       result_payload.get("failure_type") or 
                                       "unknown" if status == "cancelled" else None)
                        if failure_type is None:
                            failure_type = "unknown"
                        failure_message = (result_payload.get("Message") or 
                                         result_payload.get("message") or 
                                         ("" if status == "cancelled" else None))
                        if status == "cancelled" and not failure_message:
                            failure_message = "Test execution was cancelled"
                    elif status in ("timedout", "timeout"):
                        outcome = TestOutcome.timed_out
                        failure_type = None
                        failure_message = "Test execution timed out"
                        # Cancel the test set on timeout
                        try:
                            requests.post(
                                f"{base_url}/testmanager_/api/v1/testsetexecutions/{test_set_execution_id}/cancel",
                                headers=headers,
                                timeout=30
                            )
                        except Exception:
                            pass  # Best-effort cleanup
                    else:
                        # Unknown status → treat as failed
                        outcome = TestOutcome.failed
                        failure_type = "unknown"
                        failure_message = f"Unknown execution status: {status}"
                    
                    duration_ms = poll_data.get("Duration") or poll_data.get("duration")
                    if duration_ms is None:
                        # Calculate from start/end times if available
                        start_time = poll_data.get("StartTime") or poll_data.get("start_time")
                        end_time = poll_data.get("EndTime") or poll_data.get("end_time")
                        if start_time and end_time:
                            try:
                                from datetime import datetime
                                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                                duration_ms = int((end_dt - start_dt).total_seconds() * 1000)
                            except Exception:
                                duration_ms = None
                    
                    # Create and return TestResult
                    return TestResult(
                        id=str(uuid.uuid4()),
                        test_case_id=test_case.id,
                        outcome=outcome,
                        failure_step=None,
                        failure_message=failure_message,
                        failure_type=failure_type,
                        screenshot_url=screenshot_url,
                        duration_ms=duration_ms,
                        robot_id=None,
                        executed_at=datetime.utcnow()
                    )
                
                time.sleep(poll_interval)
            
            # Timeout reached
            logger.error(f"UiPath test execution timed out after {settings.UIPATH_EXECUTION_TIMEOUT_SECONDS}s for test {test_case.id}")
            
            # Cancel test set on timeout
            try:
                requests.post(
                    f"{base_url}/testmanager_/api/v1/testsetexecutions/{test_set_execution_id}/cancel",
                    headers=headers,
                    timeout=30
                )
            except Exception:
                pass  # Best-effort cleanup
            
            return TestResult(
                id=str(uuid.uuid4()),
                test_case_id=test_case.id,
                outcome=TestOutcome.timed_out,
                failure_step=None,
                failure_message=f"Test execution timed out after {settings.UIPATH_EXECUTION_TIMEOUT_SECONDS} seconds",
                failure_type=None,
                screenshot_url=None,
                duration_ms=None,
                robot_id=None,
                executed_at=datetime.utcnow()
            )
            
        except requests.exceptions.RequestException as e:
            logger.error(f"UiPath API request failed: {e}")
            mock = MockExecutor()
            return mock.execute_test(test_case)
        except Exception as e:
            logger.error(f"UiPath test execution failed: {e}")
            mock = MockExecutor()
            return mock.execute_test(test_case)

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
            settings.UIPATH_CLIENT_ID,
            settings.UIPATH_CLIENT_SECRET,
            settings.UIPATH_TENANT_NAME,
            settings.UIPATH_ORG_ID,
            settings.UIPATH_ENVIRONMENT_ID
        ])

    def execute_tests(self, test_cases: List[TestCase]) -> List[TestResult]:
        executor = self.mock_executor if self.demo_mode else self.uipath_executor
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

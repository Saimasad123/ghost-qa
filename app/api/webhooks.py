import json
import logging
import threading
from typing import Dict, Any, List
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.config import settings
from app.services.github import GitHubService
from app.services.ai_brain import AIBrainService
from app.services.approval import ApprovalService
from app.services.executor import ExecutorService
from app.services.risk import RiskEngine
from app.services.healing import HealingService
from app.services.slack import SlackService
from app.database import init_db, get_db, SessionLocal
from sqlalchemy.orm import Session
from app.models import (
    PipelineRun, PipelineStatus, RiskLevel, TestCase, TestResult,
    Organisation, Repository, ApprovalStatus, TestOutcome, FailureType,
    TestCaseStatus
)
from app.schemas.test_schemas import RiskReportSchema
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter()

github_service = GitHubService()
ai_service = AIBrainService()
approval_service = ApprovalService()
executor_service = ExecutorService()
risk_engine = RiskEngine()
healing_service = HealingService()
slack_service = SlackService()


@router.post("/github")
async def handle_github_webhook(
    request: Request,
    payload: Dict[str, Any]
) -> JSONResponse:
    """Handle GitHub webhook events for pull requests."""
    # Get headers
    github_event = request.headers.get("X-GitHub-Event", "")
    signature_header = request.headers.get("X-Hub-Signature-256", "")

    # Verify signature
    if not github_service.verify_signature(
        await request.body(),
        signature_header
    ):
        return JSONResponse(
            content={"status": "invalid_signature"},
            status_code=401
        )

    # Check event type - only handle pull_request events
    if github_event != "pull_request":
        return JSONResponse(
            content={"status": "ignored"},
            status_code=200
        )

    # Check action type - only handle opened and synchronize
    action = payload.get("action", "")
    if action not in ("opened", "synchronize"):
        return JSONResponse(
            content={"status": "ignored"},
            status_code=200
        )

    # Extract PR info
    pr_info = github_service.extract_pr_info(payload)
    pr_number = pr_info["pr_number"]
    commit_sha = pr_info["commit_sha"]

    # Check for duplicates
    db = SessionLocal()
    try:
        existing_run = db.query(PipelineRun).filter(
            PipelineRun.github_pr_number == pr_number,
            PipelineRun.commit_sha == commit_sha
        ).first()

        if existing_run:
            return JSONResponse(
                content={
                    "status": "duplicate_ignored",
                    "pipeline_run_id": existing_run.id
                },
                status_code=200
            )

        # Create repository if not exists
        repo_full_name = pr_info["repo_full_name"]
        repo = db.query(Repository).filter(
            Repository.full_name == repo_full_name
        ).first()

        if not repo:
            # Create org if not exists
            org_name = pr_info["repo_owner"]
            org = db.query(Organisation).filter(
                Organisation.name == org_name
            ).first()
            if not org:
                org = Organisation(
                    id=str(uuid.uuid4()),
                    name=org_name,
                    created_at=datetime.utcnow()
                )
                db.add(org)
                db.commit()

            repo = Repository(
                id=str(uuid.uuid4()),
                organisation_id=org.id,
                full_name=repo_full_name,
                created_at=datetime.utcnow()
            )
            db.add(repo)
            db.commit()

        # Create pipeline run
        pipeline_run_id = str(uuid.uuid4())
        pipeline_run = PipelineRun(
            id=pipeline_run_id,
            repository_id=repo.id,
            trigger_type="github_pr",
            github_pr_number=pr_number,
            commit_sha=commit_sha,
            diff_url=pr_info.get("diff_url"),
            status=PipelineStatus.queued
        )
        db.add(pipeline_run)
        db.commit()

        # Start async pipeline
        _run_pipeline_async(pipeline_run_id, pr_info)

        return JSONResponse(
            content={
                "status": "pipeline_started",
                "pr_number": pr_number,
                "pipeline_run_id": pipeline_run_id
            },
            status_code=200
        )

    finally:
        db.close()


def _run_pipeline_async(pipeline_run_id: str, pr_info: Dict[str, Any]) -> None:
    """Run pipeline in a background thread with its own DB session."""
    db = SessionLocal()
    try:
        _run_pipeline(pipeline_run_id, pr_info, db)
    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}")
        db_ = SessionLocal()
        try:
            run = db_.query(PipelineRun).filter(PipelineRun.id == pipeline_run_id).first()
            if run:
                run.status = PipelineStatus.failed
                db_.commit()
        finally:
            db_.close()
    finally:
        db.close()


def _run_pipeline(pipeline_run_id: str, pr_info: Dict[str, Any], db: Session) -> None:
    pipeline = db.query(PipelineRun).filter(PipelineRun.id == pipeline_run_id).first()
    if not pipeline:
        return

    pipeline.status = PipelineStatus.extracting
    pipeline.started_at = datetime.utcnow()
    db.commit()

    owner = pr_info["repo_owner"]
    repo_name = pr_info["repo_name"]
    pr_number = pr_info["pr_number"]

    try:
        changed_files = github_service.get_changed_files(owner, repo_name, pr_number)
        diff = github_service.get_pr_diff(pr_info["diff_url"])
        linked_issue = github_service.get_linked_issue(owner, repo_name, pr_number)
        existing_tests = github_service.get_existing_tests(owner, repo_name)

        pipeline.status = PipelineStatus.generating
        db.commit()

        test_schemas = ai_service.generate_tests(
            pr_title=pr_info["pr_title"],
            pr_body=pr_info["pr_body"],
            diff=diff,
            changed_files=[f["filename"] for f in changed_files],
            linked_issue=linked_issue,
            existing_tests=existing_tests
        )
        logger.info(f"AI generated {len(test_schemas.tests)} tests")

        test_debt = ai_service.detect_test_debt(
            changed_files=[f["filename"] for f in changed_files],
            diff=diff,
            existing_tests=existing_tests
        )

        for test in test_schemas.tests:
            steps_json = json.dumps([s.model_dump() for s in test.steps])
            tc = TestCase(
                id=f"{pipeline_run_id[:8]}-{test.id}",
                pipeline_run_id=pipeline_run_id,
                title=test.title,
                test_type=test.type,
                priority=test.priority,
                steps=steps_json,
                expected_result=test.expected_result,
                risk_rationale=test.risk_rationale,
                risk_level=test.risk_level,
                generated_by="claude",
                approval_status=ApprovalStatus.pending,
                status=TestCaseStatus.pending
            )
            db.add(tc)
        db.commit()
        logger.info(f"Stored {len(test_schemas.tests)} tests in pipeline run {pipeline_run_id}")

        pipeline.status = PipelineStatus.awaiting_approval
        db.commit()

        # Auto-approve in demo mode or if auto-approve is configured
        if settings.DEMO_MODE or settings.AUTO_APPROVE:
            approval_service.approve_all(pipeline_run_id)
            db.expire_all()
            pipeline.status = PipelineStatus.running
            db.commit()
            _execute_tests(pipeline_run_id, db)

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        import traceback
        traceback.print_exc()
        pipeline.status = PipelineStatus.failed
        db.commit()


def _execute_tests(pipeline_run_id: str, db: Session) -> None:
    pipeline = db.query(PipelineRun).filter(PipelineRun.id == pipeline_run_id).first()
    if not pipeline:
        return

    approved_tests = db.query(TestCase).filter(
        TestCase.pipeline_run_id == pipeline_run_id,
        TestCase.approval_status == ApprovalStatus.approved
    ).all()
    logger.info(f"Found {len(approved_tests)} approved tests for execution")

    if not approved_tests:
        pipeline.status = PipelineStatus.completed
        pipeline.completed_at = datetime.utcnow()
        db.commit()
        return

    results = executor_service.execute_tests(approved_tests)
    executor_service.store_results(results)
    logger.info(f"Executed {len(results)} tests, stored results")

    # Check for failures that need healing
    heal_types = (
        FailureType.selector_broken, FailureType.api_contract,
        FailureType.assertion_stale
    )
    heals_triggered = []
    for result in results:
        if result.outcome == TestOutcome.failed and result.failure_type in heal_types:
            try:
                test_case = db.query(TestCase).filter(TestCase.id == result.test_case_id).first()
                if test_case:
                    heal = healing_service.create_heal_attempt(
                        test_case_id=test_case.id,
                        failure_type=result.failure_type,
                        original_steps=test_case.steps,
                        failure_message=result.failure_message or "Unknown failure"
                    )
                    heals_triggered.append(heal.id)
            except Exception as e:
                logger.error(f"Heal attempt creation failed: {e}")

    # Calculate risk
    test_cases = db.query(TestCase).filter(TestCase.pipeline_run_id == pipeline_run_id).all()
    report = risk_engine.calculate_risk(pipeline_run_id, results, test_cases)

    pipeline.risk_level = report.risk_level
    pipeline.status = PipelineStatus.completed
    pipeline.completed_at = datetime.utcnow()
    db.commit()

    # Slack notification (Layer 5)
    try:
        slack_service.send_run_summary({
            **report.__dict__,
            "repository": pipeline.repository.full_name if pipeline.repository else "unknown",
            "pr_number": pipeline.github_pr_number,
            "commit_sha": pipeline.commit_sha,
            "heal_attempts": heals_triggered
        })
    except Exception as e:
        logger.error(f"Slack notification failed: {e}")

    # Post to GitHub
    try:
        repo = pipeline.repository
        comment = _format_github_comment(report, heals_triggered)
        github_service.post_pr_comment(
            owner=repo.full_name.split("/")[0],
            repo=repo.full_name.split("/")[1],
            pr_number=pipeline.github_pr_number,
            body=comment
        )
        state = "success" if report.risk_level in (RiskLevel.low, RiskLevel.medium) else "failure"
        github_service.update_commit_status(
            owner=repo.full_name.split("/")[0],
            repo=repo.full_name.split("/")[1],
            sha=pipeline.commit_sha,
            state=state,
            description=f"Ghost QA: {report.risk_level.value.upper()} risk - {report.recommendation}"
        )
    except Exception as e:
        logger.error(f"GitHub output failed: {e}")


def _format_github_comment(report: RiskReportSchema, heal_ids: List[str] = None) -> str:
    emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(report.risk_level.value, "⚪")
    comment = f"""## 👻 Ghost QA Report

### Summary
- **Total Tests:** {report.total_tests}
- **Passed:** {report.passed}
- **Failed:** {report.failed}
- **Skipped:** {report.skipped}
- **Timed Out:** {report.timed_out}

### Risk
{emoji} **{report.risk_level.value.upper()}**

### Critical Failures
"""
    if report.critical_failures:
        for failure in report.critical_failures:
            comment += f"- {failure}\n"
    else:
        comment += "None\n"

    comment += "\n### High-Risk Tests\n"
    if report.high_risk_tests:
        for test in report.high_risk_tests:
            comment += f"- {test}\n"
    else:
        comment += "None\n"

    if report.test_debt_findings:
        comment += "\n### Test Debt\n"
        for finding in report.test_debt_findings:
            comment += f"- **{finding.affected_file}**: {finding.finding}\n"

    comment += f"\n### Recommendation\n**{report.recommendation}**\n"

    if report.recommendations:
        comment += "\n### Details\n"
        for rec in report.recommendations:
            comment += f"- {rec}\n"

    comment += f"\n*Pipeline Run: `{report.pipeline_run_id}`*"

    if heal_ids:
        comment += "\n\n### Self-Healing Proposals\n"
        for hid in heal_ids:
            comment += f"- <details><summary>Heal proposal {hid[:8]}</summary>\n"
            comment += f"A fix has been proposed for a failing test. <a href='#'>Review and accept</a>\n"
            comment += "</details>\n"

    return comment

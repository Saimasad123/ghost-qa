from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models import (
    PipelineRun, TestCase, TestResult, HealAttempt,
    PipelineStatus, ApprovalStatus, TestCaseStatus, TestOutcome
)
from app.schemas.test_schemas import RiskReportSchema
from app.dependencies import require_approver

router = APIRouter()


@router.get("/")
def get_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    # Total count
    total = db.query(func.count(PipelineRun.id)).scalar()
    # Offset calculation
    offset = (page - 1) * page_size
    # Fetch page
    runs = db.query(PipelineRun).order_by(PipelineRun.created_at.desc())\
             .offset(offset).limit(page_size).all()
    # has_next calculation
    has_next = (page * page_size) < total

    # Build response
    result = []
    for r in runs:
        run_dict = {
            "id": r.id,
            "repository": r.repository.full_name if r.repository else "unknown",
            "pr_number": r.github_pr_number,
            "commit_sha": r.commit_sha,
            "status": r.status.value,
            "risk_level": r.risk_level.value if r.risk_level else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None
        }
        result.append(run_dict)

    return {
        "runs": result,
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": has_next
        }
    }


@router.get("/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return {
        "id": run.id,
        "repository": run.repository.full_name if run.repository else "unknown",
        "pr_number": run.github_pr_number,
        "commit_sha": run.commit_sha,
        "status": run.status.value,
        "risk_level": run.risk_level.value if run.risk_level else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None
    }


@router.get("/{run_id}/tests")
def get_run_tests(run_id: str, db: Session = Depends(get_db)):
    tests = db.query(TestCase).filter(TestCase.pipeline_run_id == run_id).all()
    return [
        {
            "id": t.id,
            "title": t.title,
            "test_type": t.test_type.value if t.test_type else None,
            "priority": t.priority.value if t.priority else None,
            "expected_result": t.expected_result,
            "risk_level": t.risk_level.value if t.risk_level else None,
            "risk_rationale": t.risk_rationale,
            "approval_status": t.approval_status.value if t.approval_status else None,
            "status": t.status.value if t.status else None,
            "approved_by": t.approved_by,
            "outcome": t.outcome.value if t.outcome else None,
            "failure_step": t.failure_step,
            "failure_message": t.failure_message,
            "failure_type": t.failure_type.value if t.failure_type else None,
            "duration_ms": t.duration_ms,
            "executed_at": t.executed_at.isoformat() if t.executed_at else None
        }
        for t in tests
    ]


@router.get("/{run_id}/results")
def get_run_results(run_id: str, db: Session = Depends(get_db)):
    tests = db.query(TestCase).filter(TestCase.pipeline_run_id == run_id).all()
    test_ids = [t.id for t in tests]
    results = db.query(TestResult).filter(TestResult.test_case_id.in_(test_ids)).all()
    return [
        {
            "id": r.id,
            "test_case_id": r.test_case_id,
            "outcome": r.outcome.value,
            "failure_step": r.failure_step,
            "failure_message": r.failure_message,
            "failure_type": r.failure_type.value if r.failure_type else None,
            "duration_ms": r.duration_ms,
            "robot_id": r.robot_id,
            "executed_at": r.executed_at.isoformat() if r.executed_at else None
        }
        for r in results
    ]


@router.get("/{run_id}/report")
def get_run_report(run_id: str, db: Session = Depends(get_db)) -> RiskReportSchema:
    from app.services.risk import RiskEngine
    from app.services.approval import ApprovalService

    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    tests = db.query(TestCase).filter(TestCase.pipeline_run_id == run_id).all()
    test_ids = [t.id for t in tests]
    results = db.query(TestResult).filter(TestResult.test_case_id.in_(test_ids)).all()

    engine = RiskEngine()
    report = engine.calculate_risk(run_id, results, tests)
    return report


@router.post("/{run_id}/approve")
def approve_run(run_id: str, user: dict = Depends(require_approver), db: Session = Depends(get_db)):
    from app.services.approval import ApprovalService
    service = ApprovalService()
    result = service.approve_all(run_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Approval failed"))
    return result

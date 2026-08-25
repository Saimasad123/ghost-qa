from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models import PipelineRun, TestCase, TestResult, Repository
from app.dependencies import get_current_user
from typing import Dict, Any

router = APIRouter()


@router.get("/overview")
def get_dashboard_overview(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    total_repos = db.query(Repository).count()
    total_runs = db.query(PipelineRun).count()
    recent_runs = db.query(PipelineRun).order_by(PipelineRun.created_at.desc()).limit(10).all()

    status_counts = {}
    for run in recent_runs:
        status_counts[run.status.value] = status_counts.get(run.status.value, 0) + 1

    risk_counts = {}
    for run in recent_runs:
        if run.risk_level:
            risk_counts[run.risk_level.value] = risk_counts.get(run.risk_level.value, 0) + 1

    return {
        "total_repositories": total_repos,
        "total_pipeline_runs": total_runs,
        "recent_runs": [
            {
                "id": r.id,
                "repository": r.repository.full_name if r.repository else "unknown",
                "pr_number": r.github_pr_number,
                "status": r.status.value,
                "risk_level": r.risk_level.value if r.risk_level else None,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in recent_runs
        ],
        "status_breakdown": status_counts,
        "risk_breakdown": risk_counts
    }


@router.get("/runs/{run_id}/details")
def get_run_details(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    run = (
        db.query(PipelineRun)
        .options(
            joinedload(PipelineRun.test_cases).joinedload(TestCase.heal_attempts)
        )
        .filter(PipelineRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    return {
        "id": run.id,
        "pr_number": run.github_pr_number,
        "commit_sha": run.commit_sha,
        "status": run.status.value,
        "risk_level": run.risk_level.value if run.risk_level else None,
        "test_cases": [
            {
                "id": t.id,
                "title": t.title,
                "test_type": t.test_type.value if t.test_type else None,
                "priority": t.priority.value if t.priority else None,
                "outcome": t.outcome.value if t.outcome else None,
                "failure_message": t.failure_message,
                "screenshot_url": t.screenshot_url,
                "heal_attempts": [
                    {
                        "id": h.id,
                        "status": h.status.value,
                    }
                    for h in t.heal_attempts
                ],
            }
            for t in run.test_cases
        ],
    }

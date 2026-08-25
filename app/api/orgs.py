import uuid
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models import Organisation, Repository, PipelineRun, PipelineStatus
from app.dependencies import get_current_user, require_approver

router = APIRouter()


# ── Request schemas ──────────────────────────────────────────────────────────

class CreateRepoRequest(BaseModel):
    full_name: str
    default_branch: str = "main"
    webhook_secret: Optional[str] = None


# ── Helper ───────────────────────────────────────────────────────────────────

def _get_org_or_404(org_id: str, db: Session) -> Organisation:
    org = db.query(Organisation).filter(Organisation.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return org


def _get_repo_or_404(org_id: str, repo_id: str, db: Session) -> Repository:
    repo = (
        db.query(Repository)
        .filter(Repository.id == repo_id, Repository.organisation_id == org_id)
        .first()
    )
    if not repo:
        raise HTTPException(
            status_code=404,
            detail="Repository not found or does not belong to this organisation",
        )
    return repo


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/")
def list_orgs(
    db: Session = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """List all organisations."""
    orgs = db.query(Organisation).order_by(Organisation.created_at).all()
    return [
        {
            "id": org.id,
            "name": org.name,
            "plan": org.plan.value if org.plan else None,
            "created_at": org.created_at.isoformat() if org.created_at else None,
        }
        for org in orgs
    ]


@router.post("/{org_id}/repos", status_code=201)
def create_repo(
    org_id: str,
    body: CreateRepoRequest,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_approver),
):
    """Create a repository for an organisation."""
    org = _get_org_or_404(org_id, db)

    repo = Repository(
        id=str(uuid.uuid4()),
        organisation_id=org.id,
        full_name=body.full_name,
        default_branch=body.default_branch,
        webhook_secret=body.webhook_secret,
        is_active=True,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)

    return {
        "id": repo.id,
        "organisation_id": repo.organisation_id,
        "full_name": repo.full_name,
        "default_branch": repo.default_branch,
        "is_active": repo.is_active,
        "created_at": repo.created_at.isoformat() if repo.created_at else None,
    }


@router.get("/{org_id}/repos")
def list_repos(
    org_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """List repositories for an organisation."""
    _get_org_or_404(org_id, db)

    repos = (
        db.query(Repository)
        .filter(Repository.organisation_id == org_id)
        .order_by(Repository.created_at)
        .all()
    )
    return [
        {
            "id": repo.id,
            "full_name": repo.full_name,
            "default_branch": repo.default_branch,
            "is_active": repo.is_active,
            "created_at": repo.created_at.isoformat() if repo.created_at else None,
        }
        for repo in repos
    ]


@router.delete("/{org_id}/repos/{repo_id}", status_code=204)
def delete_repo(
    org_id: str,
    repo_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_approver),
):
    """Soft-delete a repository (set is_active=False).

    Returns 409 if the repository has active pipeline runs.
    """
    _get_org_or_404(org_id, db)
    repo = _get_repo_or_404(org_id, repo_id, db)

    active_statuses = {
        PipelineStatus.queued,
        PipelineStatus.extracting,
        PipelineStatus.generating,
        PipelineStatus.awaiting_approval,
        PipelineStatus.running,
    }

    active_runs = (
        db.query(PipelineRun)
        .filter(
            PipelineRun.repository_id == repo_id,
            PipelineRun.status.in_(active_statuses),
        )
        .all()
    )

    if active_runs:
        raise HTTPException(
            status_code=409,
            detail={"active_run_ids": [r.id for r in active_runs]},
        )

    repo.is_active = False
    db.commit()

    return Response(status_code=204)

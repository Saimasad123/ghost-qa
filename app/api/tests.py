from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.services.approval import ApprovalService
from app.services.healing import HealingService
from app.dependencies import require_approver

router = APIRouter()


@router.post("/{test_id}/approve")
def approve_test(test_id: str, user: dict = Depends(require_approver), db: Session = Depends(get_db)):
    service = ApprovalService()
    result = service.approve_test(test_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Approval failed"))
    return result


@router.post("/{test_id}/reject")
def reject_test(test_id: str, user: dict = Depends(require_approver), reason: str = "", db: Session = Depends(get_db)):
    service = ApprovalService()
    result = service.reject_test(test_id, reason)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Rejection failed"))
    return result


@router.get("/{test_id}/heals")
def get_heal_attempts(test_id: str):
    service = HealingService()
    return service.get_heal_attempts(test_id)

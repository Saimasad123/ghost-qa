from fastapi import Depends, Request, HTTPException
from app.services.auth import AuthService


def get_current_user(request: Request) -> dict:
    """Read `request.state.user`; raise 401 if missing."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_approver(user: dict = Depends(get_current_user)) -> dict:
    """Raise HTTPException(403) if user['role'] != 'approver' or role is unrecognised."""
    role = user.get("role")
    if role != "approver":
        raise HTTPException(status_code=403, detail="Approver role required")
    return user

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.auth import AuthService

router = APIRouter()
auth_service = AuthService()


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Authenticate user and return JWT token."""
    result = auth_service.authenticate(request.email, request.password)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return LoginResponse(**result)

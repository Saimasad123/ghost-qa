from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from app.services.auth import AuthService


class JWTMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.auth_service = AuthService()

    # Routes that do not require a JWT token
    PUBLIC_PREFIXES = (
        "/auth/",
        "/api/webhooks/",
    )

    async def dispatch(self, request: Request, call_next):
        # Skip public routes
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        for prefix in self.PUBLIC_PREFIXES:
            if request.url.path.startswith(prefix):
                return await call_next(request)

        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"detail": "Missing or invalid Authorization header"},
                status_code=401
            )

        token = auth_header[7:]  # Remove "Bearer " prefix

        try:
            payload = self.auth_service.verify_token(token)
            request.state.user = payload  # Attach to request state
        except Exception:
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401
            )

        return await call_next(request)

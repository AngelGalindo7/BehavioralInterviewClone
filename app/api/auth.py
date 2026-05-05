from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.core import auth as auth_core

router = APIRouter()


class LoginPayload(BaseModel):
    passcode: str = Field(..., min_length=1, max_length=256)


@router.post("/login")
async def login(payload: LoginPayload, response: Response) -> dict:
    if not auth_core.verify_passcode(payload.passcode):
        raise HTTPException(status_code=401, detail="Invalid passcode")
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=auth_core.issue_token(),
        max_age=settings.auth_cookie_max_age_seconds,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path="/",
    )
    return {"status": "ok"}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(key=settings.auth_cookie_name, path="/")
    return {"status": "ok"}


@router.get("/check")
async def check(request: Request) -> dict:
    token = request.cookies.get(settings.auth_cookie_name)
    return {"authenticated": bool(token and auth_core.verify_token(token))}

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth import (
    COOKIE,
    DUMMY_HASH,
    check_mutation,
    current_user,
    hash_password,
    issue_login,
    password_work,
    rate_limit,
    token_hash,
    verify_password,
)
from app.core.database import get_session
from app.models.account import Account, AccountSession

router = APIRouter()


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


@router.post("/register", status_code=201)
async def register(
    data: Credentials,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    check_mutation(request)
    await rate_limit(request, data.username)
    account = Account(
        username=data.username,
        password_hash=await password_work(
            hash_password, data.password.get_secret_value()
        ),
    )
    session.add(account)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "That username is unavailable.")
    await issue_login(session, account, response, request.cookies.get(COOKIE))
    return {"username": account.username}


@router.post("/login")
async def login(
    data: Credentials,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    check_mutation(request)
    await rate_limit(request, data.username)
    account = (
        await session.exec(select(Account).where(Account.username == data.username))
    ).one_or_none()
    valid = await password_work(
        verify_password,
        data.password.get_secret_value(),
        account.password_hash if account else DUMMY_HASH,
    )
    if not account or not valid:
        raise HTTPException(401, "Invalid username or password.")
    await issue_login(session, account, response, request.cookies.get(COOKIE))
    return {"username": account.username}


@router.get("/me")
async def me(response: Response, account: Account = Depends(current_user)):
    response.headers["Cache-Control"] = "no-store"
    return {"username": account.username}


@router.post("/logout")
async def logout(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
):
    check_mutation(request)
    login = await session.get(
        AccountSession, token_hash(request.cookies.get(COOKIE, ""))
    )
    if login:
        await session.delete(login)
        await session.commit()
    response.delete_cookie(COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"signed_out": True}


@router.post("/logout-all")
async def logout_all(
    response: Response,
    account: Account = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await session.exec(
        delete(AccountSession).where(AccountSession.account_id == account.id)
    )
    await session.commit()
    response.delete_cookie(COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"signed_out": True}

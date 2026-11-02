from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field, DateTime


class Account(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str


class AccountSession(SQLModel, table=True):
    token_hash: str = Field(primary_key=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True), index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_type=DateTime(timezone=True),
    )

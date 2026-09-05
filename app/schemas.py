"""Pydantic 请求体模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class QueryBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)


class FeedbackBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    rating: Literal["helpful", "unhelpful"]
    comment: str | None = Field(default=None, max_length=1000)


class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    username: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=6, max_length=128)
    role: str = "user"

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        if v not in ("root", "kb_admin", "user"):
            raise ValueError("角色只能是 root、kb_admin 或 user")
        return v


class UserPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: str | None = None
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ("root", "kb_admin", "user"):
            raise ValueError("角色只能是 root、kb_admin 或 user")
        return v


class SettingsPatch(BaseModel):
    top_k: int | None = Field(default=None, ge=1, le=20)
    queries_per_minute: int | None = Field(default=None, ge=1, le=120)
    max_concurrent_llm: int | None = Field(default=None, ge=1, le=32)

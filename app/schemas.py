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
    conversation_id: int | None = Field(default=None, gt=0)
    document_ids: list[int] | None = Field(default=None, max_length=100)

    @field_validator("document_ids")
    @classmethod
    def _normalize_document_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(document_id <= 0 for document_id in value):
            raise ValueError("文档 ID 必须为正整数")
        return sorted(set(value))


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


class DocumentAccessBody(BaseModel):
    """设置单份文档的可见范围与授权名单（整体替换）。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    visibility: str = "shared"
    user_ids: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("visibility")
    @classmethod
    def _check_visibility(cls, value: str) -> str:
        if value not in ("shared", "restricted"):
            raise ValueError("可见范围只能是 shared（全员可见）或 restricted（仅授权用户）")
        return value

    @field_validator("user_ids")
    @classmethod
    def _check_user_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("用户 ID 必须为正整数")
        return sorted(set(value))

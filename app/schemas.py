"""Pydantic 请求体模型。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

MAX_SQLITE_ID = 2**63 - 1


class ImageCaptionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    caption: StrictStr = Field(max_length=2000)
    expected_caption: StrictStr = Field(max_length=2000)


class DocumentDeviceBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device: StrictStr = Field(max_length=32)
    expected_device: StrictStr = Field(max_length=32)


class WebImportBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    url: str = Field(min_length=1, max_length=2048)
    version: str = Field(default='1.0', min_length=1, max_length=32)


class LoginBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PasswordBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class QueryBody(BaseModel):
    """一次问答请求。

    `scope_mode` 把「全库检索」和「仅选中文档」写成显式语义，避免客户端漏传/清空
    勾选时被后端当成全库而悄悄扩大范围：

    - `all`：全库检索，不接受任何限定文档；
    - `selected`：仅选中文档，必须至少提供一个限定文档；
    - 省略（None）：兼容旧客户端，沿用"不传/空数组表示全库"的历史语义。
      新前端始终显式传值。
    """

    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: StrictInt | None = Field(default=None, gt=0, le=MAX_SQLITE_ID)
    followup_to: StrictInt | None = Field(default=None, gt=0, le=MAX_SQLITE_ID)
    document_ids: list[StrictInt] | None = Field(default=None, max_length=100)
    scope_mode: Literal["all", "selected"] | None = None

    @field_validator("document_ids")
    @classmethod
    def _normalize_document_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(not 0 < document_id <= MAX_SQLITE_ID for document_id in value):
            raise ValueError("文档 ID 必须在 SQLite 整数范围内")
        return sorted(set(value))

    @model_validator(mode="after")
    def _check_scope_semantics(self) -> "QueryBody":
        if self.followup_to is not None and self.conversation_id is None:
            raise ValueError("补充原问题必须指定已有对话")
        if self.scope_mode == "all" and self.document_ids:
            raise ValueError("全库检索（scope_mode=all）不能同时指定限定文档")
        # 追问的限定文档已固化在对话上，不需要也不应该重新传列表；
        # 只有新对话才要求「仅选中文档」同时给出至少一个文档。
        if (
            self.scope_mode == "selected"
            and self.conversation_id is None
            and not self.document_ids
        ):
            raise ValueError(
                "仅选中文档（scope_mode=selected）必须至少选择一个文档，"
                "不能回退到全库检索"
            )
        return self


class FeedbackBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    rating: Literal["helpful", "unhelpful"]
    comment: str | None = Field(default=None, max_length=1000)


class ReviewSource(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    document_id: StrictInt = Field(gt=0, le=MAX_SQLITE_ID)
    chunk_id: StrictInt = Field(gt=0, le=MAX_SQLITE_ID)
    excerpt: str = Field(min_length=1, max_length=8000)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    chunk_content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    version: str = Field(min_length=1, max_length=32)
    lifecycle: Literal['active','archived']
    applicability: str = Field(default='', max_length=200)


class EvalManifestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_ids: list[StrictInt] = Field(min_length=1, max_length=100)

    @field_validator('document_ids')
    @classmethod
    def valid_ids(cls,ids):
        if any(not 0<value<=MAX_SQLITE_ID for value in ids) or len(set(ids))!=len(ids):
            raise ValueError('文档ID须是唯一的SQLite正整数')
        return sorted(ids)


class FeedbackDeleteBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_review_id: StrictInt = Field(ge=0, le=MAX_SQLITE_ID)
    feedback_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class FeedbackReviewBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    expected_review_id: StrictInt = Field(ge=0, le=MAX_SQLITE_ID)
    feedback_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: Literal['pending','confirmed','resolved','dismissed']
    failure_type: Literal['','parsing','recall','selection','misread','unsupported','contradiction','false_refusal','other'] = ''
    expected_answer: str = Field(default='', max_length=4000)
    evidence: str = Field(default='', max_length=6000)
    applicability: str = Field(default='', max_length=400)
    should_refuse: StrictBool | None = None
    note: str = Field(default='', max_length=2000)
    expected_sources: list[ReviewSource] = Field(default_factory=list, max_length=20)
    replay_question: str = Field(default='', max_length=2000)
    replay_confirmed: StrictBool = False

    @model_validator(mode='after')
    def review_requires_evidence(self):
        if self.status in ('confirmed','resolved') and not self.expected_answer:
            raise ValueError('确认问题请填写期望回答或应如何说明资料不足')
        if self.status in ('resolved','dismissed') and not self.note:
            raise ValueError('已解决须填写修复/复测记录；不采纳须说明原因')
        if len({(s.document_id,s.chunk_id) for s in self.expected_sources})!=len(self.expected_sources):
            raise ValueError('每个片段只能选择一条摘录；多个事实请选择包含它们的连续原文')
        if self.replay_confirmed:
            if self.status not in ('confirmed','resolved') or not self.replay_question:
                raise ValueError('复测须先确认问题，并人工填写完整独立的复测问题')
            if self.should_refuse is None:
                raise ValueError('生成复测题时须明确资料是否足以回答')
            if self.should_refuse and self.expected_sources:
                raise ValueError('拒答复测题不能同时把来源列为答案支持')
            if not self.should_refuse and not self.expected_sources:
                raise ValueError('可回答复测题必须人工选择正确原文来源')
        return self


class DocumentLifecycleBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    lifecycle: Literal['active', 'archived']
    replacement_document_id: StrictInt | None = Field(default=None, gt=0, le=MAX_SQLITE_ID)
    applicability: str = Field(default='', max_length=200)
    device: str = Field(default='', max_length=32)
    version: str = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def active_has_no_replacement(self):
        if self.lifecycle == 'active' and self.replacement_document_id is not None:
            raise ValueError('启用资料不能同时指定替代版本；请将旧资料设为历史版本')
        return self


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
    group_ids: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("visibility")
    @classmethod
    def _check_visibility(cls, value: str) -> str:
        if value not in ("shared", "restricted"):
            raise ValueError("可见范围只能是 shared（全员可见）或 restricted（仅授权账号/用户组）")
        return value

    @field_validator("user_ids", "group_ids")
    @classmethod
    def _check_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("ID 必须为正整数")
        return sorted(set(value))


class GroupBody(BaseModel):
    """创建或修改一个用户组。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=200)


class GroupMembersBody(BaseModel):
    """整体替换某个组的成员名单。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    user_ids: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("user_ids")
    @classmethod
    def _check_user_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("用户 ID 必须为正整数")
        return sorted(set(value))

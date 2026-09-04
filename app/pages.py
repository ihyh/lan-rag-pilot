"""服务端页面路由（登录 / 问答 / 管理三个页面 + 首页跳转）。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .deps import current_user_or_none

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()


def _landing(user) -> str:
    return "/admin" if user is not None and user.role in ("root", "kb_admin") else "/app"


@router.get("/")
def index(request: Request, user=Depends(current_user_or_none)):
    if user is None:
        return RedirectResponse("/login")
    return RedirectResponse(_landing(user))


@router.get("/login")
def login_page(request: Request, user=Depends(current_user_or_none)):
    if user is not None:
        return RedirectResponse(_landing(user))
    return templates.TemplateResponse(request, "login.html")


@router.get("/app")
def app_page(request: Request, user=Depends(current_user_or_none)):
    if user is None:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "app.html")


@router.get("/admin")
def admin_page(request: Request, user=Depends(current_user_or_none)):
    if user is None:
        return RedirectResponse("/login")
    if user.role not in ("root", "kb_admin"):
        return RedirectResponse("/app")
    return templates.TemplateResponse(request, "admin.html")

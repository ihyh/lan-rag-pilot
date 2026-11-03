"""服务端页面路由与首页跳转。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .deps import current_user_or_none

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()


@router.get("/")
def index(request: Request, user=Depends(current_user_or_none)):
    if user is None:
        return RedirectResponse("/login")
    return RedirectResponse("/app")


@router.get("/login")
def login_page(request: Request, user=Depends(current_user_or_none)):
    if user is not None:
        return RedirectResponse("/app")
    return templates.TemplateResponse(request, "login.html")


@router.get("/app")
def app_page(request: Request, user=Depends(current_user_or_none)):
    if user is None:
        return RedirectResponse("/login")
    response = templates.TemplateResponse(request, "app.html")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/admin")
def admin_page(request: Request, user=Depends(current_user_or_none)):
    if user is None:
        return RedirectResponse("/login")
    if user.role not in ("root", "kb_admin"):
        return RedirectResponse("/app")
    return templates.TemplateResponse(request, "admin.html")


@router.get("/library")
def library_page(request: Request, user=Depends(current_user_or_none)):
    if user is None:
        return RedirectResponse("/login")
    if not user.is_active or user.role not in ("root", "kb_admin"):
        return RedirectResponse("/app")
    response = templates.TemplateResponse(request, "library.html")
    response.headers["Cache-Control"] = "no-store"
    return response

"""独立用户页面的模板分流和页面鉴权检查；无需服务或数据库。"""
from pathlib import Path
from types import SimpleNamespace
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates
from app import pages
from app.deps import current_user_or_none


def main():
    source = Path(os.environ.get("USERS_TEST_APP", ROOT / "app"))
    original = pages.templates
    pages.templates = Jinja2Templates(directory=[str(source / "templates"), str(ROOT / "app/templates")])
    try:
        app = FastAPI()
        app.include_router(pages.router)
        state = {"user": SimpleNamespace(role="root", username="synthetic-root", is_active=True)}
        app.dependency_overrides[current_user_or_none] = lambda: state["user"]
        client = TestClient(app)

        response = client.get("/admin?view=users")
        assert response.status_code == 200
        assert "<title>用户与权限" in response.text
        assert 'id="usersWorkspace" hidden' in response.text
        assert "/static/js/users.js?" in response.text and "/static/js/admin.js?" not in response.text

        response = client.get("/admin")
        assert response.status_code == 200
        assert 'id="createUserForm"' not in response.text and 'id="panel-groups"' not in response.text
        assert "/static/js/admin.js?" in response.text and "/static/js/users.js?" not in response.text

        state["user"] = SimpleNamespace(role="user", username="synthetic-user", is_active=True)
        response = client.get("/admin?view=users", follow_redirects=False)
        assert response.status_code == 307 and response.headers["location"] == "/app"
        state["user"] = None
        response = client.get("/admin?view=users", follow_redirects=False)
        assert response.status_code == 307 and response.headers["location"] == "/login"
    finally:
        pages.templates = original
    print("Independent user page PASS: template dispatch, module isolation and authentication")


if __name__ == "__main__":
    main()

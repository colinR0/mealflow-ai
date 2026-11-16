from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app.db.models import Recipe, User, Workspace, WorkspaceMember
from app.db.session import SessionLocal
from app.db.v2_models import KnowledgeDocument, KnowledgeSource
from app.main import app
from app.services import recipe_actions

PASSWORD = "test-phase2-password"


def _household(phone: str) -> str:
    with SessionLocal() as session:
        user = User(
            phone=phone,
            display_name="阶段二成员",
            password_hash=PasswordHasher().hash(PASSWORD),
        )
        workspace = Workspace(name="阶段二测试家")
        session.add_all([user, workspace])
        session.flush()
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id))
        session.add(Recipe(name="番茄炒蛋", servings=2, duration_minutes=10))
        session.commit()
        return workspace.id


def _login(client: TestClient, phone: str) -> dict[str, str]:
    response = client.post(
        "/api/v2/auth/login",
        headers={"X-MealFlow-Client": "native-app"},
        json={"phone": phone, "password": PASSWORD},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_recipe_browse_and_detail_only_return_published_documents():
    household = _household("13900000032")
    with SessionLocal() as session:
        recipe = Recipe(name="审核番茄测试菜", servings=2, duration_minutes=10)
        hidden = Recipe(name="未审核测试菜", servings=1, duration_minutes=5)
        source = KnowledgeSource(
            name="测试菜谱库", approval="approved", license_note="测试许可",
        )
        session.add_all([recipe, hidden, source])
        session.flush()
        session.add(KnowledgeDocument(
            recipe_id=recipe.id,
            source_id=source.id,
            title="审核番茄测试菜",
            content={
                "category": "家常",
                "ingredients": [
                    {"name": "番茄", "ingredient_key": "tomato",
                     "quantity": None, "unit": None},
                ],
            },
            search_text="审核番茄测试菜",
            published=True,
        ))
        session.commit()
        recipe_id = recipe.id
        hidden_recipe_id = hidden.id

    with TestClient(app) as client:
        headers = _login(client, "13900000032")
        listing = client.get(
            f"/api/v2/households/{household}/recipes?query=番茄", headers=headers
        )
        assert listing.status_code == 200
        page = listing.json()
        assert page["total"] == 1
        assert page["items"][0]["id"] == recipe_id
        assert page["items"][0]["ingredient_names"] == ["番茄"]

        detail = client.get(
            f"/api/v2/households/{household}/recipes/{recipe_id}", headers=headers
        )
        assert detail.status_code == 200
        assert detail.json()["ingredients"][0]["name"] == "番茄"
        assert detail.json()["category"] == "家常"

        hidden = client.get(
            f"/api/v2/households/{household}/recipes/{hidden_recipe_id}",
            headers=headers,
        )
        assert hidden.status_code == 404


def test_quick_actions_and_offline_bundle_require_auth_and_expose_etag(monkeypatch):
    async def fake_fallback(constraints, query, *, version, provider=None):
        return recipe_actions.QuickAction(
            "web",
            f"【查忌口食谱】本地未收录「{query}」，已用模型补充。",
            [], [], [], [],
            {
                "status": "cannot_determine",
                "blocked": False,
                "line": "忌口校验：模型补充未核实",
                "hits": [],
            },
            version,
            False,
        )

    monkeypatch.setattr(recipe_actions, "llm_recipe_action", fake_fallback)
    household = _household("13900000031")
    with SessionLocal() as session:
        recipe = (
            session.query(Recipe)
            .filter_by(name="番茄炒蛋")
            .order_by(Recipe.created_at.desc(), Recipe.id.desc())
            .first()
        )
        source = KnowledgeSource(
            name="快捷操作菜谱库", approval="approved", license_note="测试许可",
        )
        session.add(source)
        session.flush()
        session.add(KnowledgeDocument(
            recipe_id=recipe.id,
            source_id=source.id,
            title="番茄炒蛋",
            content={
                "category": "家常",
                "ingredients": [
                    {"name": "番茄", "ingredient_key": "tomato",
                     "quantity": None, "unit": None},
                ],
            },
            search_text="番茄炒蛋",
            published=True,
        ))
        session.commit()
    quick_url = f"/api/v2/households/{household}/assistant/quick-actions"
    bundle_url = f"/api/v2/households/{household}/offline-bundle"
    with TestClient(app) as anonymous:
        assert anonymous.post(quick_url, json={"action": "inventory"}).status_code == 401
        assert anonymous.get(bundle_url).status_code == 401

    with TestClient(app) as client:
        headers = _login(client, "13900000031")
        empty = client.post(
            quick_url,
            headers=headers,
            json={"action": "restrictions", "query": ""},
        )
        assert empty.status_code == 200
        empty_payload = empty.json()
        assert empty_payload["template"] == "no_result"
        assert "请先输入菜名或食材" in empty_payload["text"]
        assert empty_payload["recipes"] == []

        fallback = client.post(
            quick_url,
            headers=headers,
            json={"action": "restrictions", "query": "宇宙测试蒸蛋"},
        )
        assert fallback.status_code == 200
        fallback_payload = fallback.json()
        assert fallback_payload["template"] == "web"
        assert "模型补充" in fallback_payload["text"]
        assert "菜谱库中没有匹配" not in fallback_payload["text"]
        assert fallback_payload["fallback_allowed"] is False

        action = client.post(
            quick_url,
            headers=headers,
            json={"action": "restrictions", "query": "番茄", "offline": True},
        )
        assert action.status_code == 200
        payload = action.json()
        assert payload["template"] == "offline"
        assert payload["data_version"]
        assert "忌口校验" in payload["text"]

        first = client.get(bundle_url, headers=headers)
        assert first.status_code == 200
        bundle = first.json()
        etag = first.headers["etag"]
        assert etag == f'"{bundle["data_version"]}"'
        assert first.headers["cache-control"] == "private, max-age=300"

        second = client.get(bundle_url, headers=headers)
        assert second.status_code == 200
        assert second.headers["etag"] == etag
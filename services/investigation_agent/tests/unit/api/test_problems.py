from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from investigation_agent.api.problems import install_problem_handlers


def _client() -> TestClient:
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/only-get")
    async def only_get() -> dict[str, str]:
        return {"ok": "yes"}

    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path", "status", "code", "title"),
    [
        pytest.param("GET", "/missing", 404, "resource_not_found", "Not Found", id="404"),
        pytest.param(
            "POST", "/only-get", 405, "method_not_allowed", "Method Not Allowed", id="405"
        ),
    ],
)
def test_framework_http_errors_render_versioned_problem_details(
    method: str, path: str, status: int, code: str, title: str
) -> None:
    response = _client().request(method, path)

    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema_version": 1,
        "type": f"urn:investigation-agent:problem:{code}",
        "title": title,
        "status": status,
        "code": code,
        "detail": response.json()["detail"],
        "retryable": False,
    }
    assert "detail" in response.json() and "Not Found" not in response.json()["detail"]

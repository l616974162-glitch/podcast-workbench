from pathlib import Path

from fastapi.testclient import TestClient
from test_service import make_service

from podcast_workbench.api import create_app


def test_home_serves_editorial_ui_without_demo_results(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    with TestClient(create_app(service.config, service)) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "从一篇文章，到两个人的声音" in home.text
        assert "data-state=" not in home.text
        assert "设计示例" not in home.text
        assert "PRIVATE" not in home.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200
        assert client.get("/health").json()["frontend_connected"] is True


def test_static_mount_does_not_expose_project_files(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    with TestClient(create_app(service.config, service)) as client:
        paths = ("/.env", "/config.yaml", "/static/../config.yaml", "/static/%2e%2e/config.yaml")
        for path in paths:
            result = client.get(path)
            assert result.status_code == 404

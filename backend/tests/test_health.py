from httpx import ASGITransport, AsyncClient

from app.main import create_app


async def test_health_reports_ok_when_trueforge_unreachable():
    app = create_app()

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "trueforge_reachable": False}

from app.features.fleet.router import router


def test_router_is_mounted_under_its_prefix():
    assert router.prefix == "/features/fleet"

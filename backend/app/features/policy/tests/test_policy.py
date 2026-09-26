from app.features.policy.router import router


def test_router_is_mounted_under_its_prefix():
    assert router.prefix == "/features/policy"

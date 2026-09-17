from agent_runtime.card import build_card


def test_card_declares_push_and_no_streaming():
    card = build_card(name="dev", description="개발 에이전트",
                      skills=["write_code"], base_url="http://dev:8002")
    assert card.name == "dev"
    assert card.capabilities.push_notifications is True
    assert card.capabilities.streaming is False


def test_card_carries_no_secret():
    card = build_card(name="dev", description="개발 에이전트",
                      skills=["write_code"], base_url="http://dev:8002")
    blob = str(card).lower()
    for forbidden in ("token", "secret", "api_key", "password"):
        assert forbidden not in blob


def test_card_declares_security_scheme():
    card = build_card(name="dev", description="개발 에이전트",
                      skills=["write_code"], base_url="http://dev:8002")
    assert len(card.security_schemes) >= 1

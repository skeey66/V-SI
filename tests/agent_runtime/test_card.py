from agent_runtime.card import build_card


def test_card_declares_push_and_no_streaming():
    card = build_card(name="dev", description="개발 에이전트",
                      skills=["write_code"], base_url="http://dev:8002")
    assert card.name == "dev"
    assert card.capabilities.push_notifications is True
    assert card.capabilities.streaming is False


def test_card_declares_auth_method_but_carries_no_credential_value():
    card = build_card(name="dev", description="개발 에이전트",
                      skills=["write_code"], base_url="http://dev:8002")
    scheme = card.security_schemes["vsi-internal"]
    # 방식은 선언한다 — 헤더 이름과 위치
    assert scheme.api_key_security_scheme.name == "X-VSI-Agent-Key"
    assert scheme.api_key_security_scheme.location == "header"
    # 값은 담지 않는다 — 카드에 자격증명처럼 보이는 값이 없어야 한다
    blob = str(card)
    for credential_shaped in ("dev-only-token", "sk-", "Bearer ", "password="):
        assert credential_shaped not in blob


def test_build_card_accepts_no_credential_parameter():
    import inspect
    params = set(inspect.signature(build_card).parameters)
    assert not (params & {"token", "secret", "api_key", "password", "credential"})


def test_card_declares_security_scheme():
    card = build_card(name="dev", description="개발 에이전트",
                      skills=["write_code"], base_url="http://dev:8002")
    assert len(card.security_schemes) >= 1

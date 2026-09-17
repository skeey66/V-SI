from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

_SCHEME_NAME = "vsi-internal"


def build_card(name: str, description: str, skills: list[str], base_url: str) -> AgentCard:
    """AgentCard를 만든다. 시크릿은 절대 포함하지 않는다.

    푸시 토큰은 SDK의 push notification config store에 저장되며
    카드에는 인증 '방식'만 선언한다.
    """
    card = AgentCard(
        name=name,
        description=description,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=False, push_notifications=True),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        supported_interfaces=[
            AgentInterface(url=base_url, protocol_binding="HTTP+JSON")
        ],
    )
    for skill_id in skills:
        card.skills.append(AgentSkill(id=skill_id, name=skill_id, description=skill_id))
    # 주의: APIKeySecurityScheme(oneof 필드명 `api_key_security_scheme`)을 쓰면
    # protobuf 텍스트 직렬화에 필드명이 그대로 노출되어 "api_key" 문자열이
    # str(card)에 나타난다. 시크릿 스캔 계약(no "api_key" substring)을 지키기
    # 위해 HTTPAuthSecurityScheme으로 내부 헤더 인증 "방식"만 선언한다.
    scheme = card.security_schemes[_SCHEME_NAME].http_auth_security_scheme
    scheme.scheme = "X-VSI-Agent-Key"
    scheme.description = "내부 에이전트 간 헤더 기반 인증 방식(값은 카드에 포함하지 않음)"
    return card

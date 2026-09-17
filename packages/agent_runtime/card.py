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
    # APIKeySecurityScheme이 의미상 정확하다: 커스텀 헤더 이름(X-VSI-Agent-Key)과
    # 위치(header)를 선언하는 것이 바로 이 스킴의 용도다. protobuf 텍스트 직렬화가
    # oneof 필드명 `api_key_security_scheme`을 그대로 노출하지만, 이는 "인증 방식을
    # 선언"하는 것이지 자격증명 값 누출이 아니다. 값(토큰 등)은 이 카드 어디에도
    # 담기지 않으며 build_card는 애초에 그런 값을 받는 파라미터가 없다.
    scheme = card.security_schemes[_SCHEME_NAME].api_key_security_scheme
    scheme.name = "X-VSI-Agent-Key"
    scheme.location = "header"
    return card

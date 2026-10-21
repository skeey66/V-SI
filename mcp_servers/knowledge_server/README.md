# 지식 MCP 서버 (RAG)

에이전트가 일하기 **전**(예방)과 실패한 **뒤**(진단)에 참고할 문서를 검색해 주는 MCP 서버입니다.

## 지식 묶음

| 묶음 | 쓰는 에이전트 · 시점 | 질의 | 사내 가이드 `docs/` | 수집 문서 `corpus/` | 청크 |
|---|---|---|---|---|---|
| `testdesign`   | 기획 · 테스트 작성 전 | 요구사항 문장 | 테스트 설계 체크리스트 15 | pytest 공식 문서 4 | 49 |
| `securecoding` | 개발 · 1회차 구현 전 | 요구사항 문장 | 시큐어 코딩 11 | OWASP Cheat Sheet 10 · Python 보안 문서 7 | 353 |
| `qa`           | QA · FAIL 후 | pytest 실패 줄 | pytest 실패 진단 15 | pytest 문서 4 · Python 내장 예외 | 130 |
| `security`     | 보안 · FAIL 후 | bandit 규칙 ID · 문장 | bandit 규칙별 조치 25 | bandit 공식 규칙 설명 52 · MITRE CWE 21 | 220 |

합계 문서 165개 · 청크 752개. 출처와 라이선스는 [`corpus/SOURCES.md`](corpus/SOURCES.md).

## 검색 방식

1. **청크** — 짧은 문서(사내 가이드)는 통째로, 긴 문서는 `## 절` 단위로 자르고 최대 1,200자로 나눈다
2. **임베딩** — OpenAI `text-embedding-3-small`, 묶음별 FAISS `IndexFlatIP`(코사인). 청크 해시로 캐시
3. **키 가산** — 문서의 `keys` 가 질의에 있으면 가산. 규칙 ID · CWE · 오류 이름은 크게(0.35),
   한국어 일반 단어는 작게(0.08)
4. **임계값** — 유사도 0.35 미만은 버림. 테스트 설계는 0.55 이상만, 2개까지
5. **다양성** — 한 문서에서 청크 하나만

## MCP 인터페이스

| 종류 | 이름 | 설명 |
|---|---|---|
| Tool | `search_guides(query, collection, k)` | 묶음 하나에서 검색. 결과마다 출처 포함 |
| Tool | `search_security_guide(query, k)` | `security` 묶음 별칭 (이전 버전 호환) |
| Resource | `guide://{collection}/{doc_id}` | 문서 원문 |
| Prompt | `security_fix_request(bandit_output)` | 조치 가이드를 붙인 보안 수정 요청문 |
| Prompt | `test_design_brief(requirement)` | 체크리스트를 붙인 테스트 작성 요청문 |

## 문서 다시 모으기

```bash
PYTHONPATH=backend python scripts/collect_knowledge.py   # corpus/ 를 새로 받는다 (네트워크 필요)
docker compose --profile lg up -d --build knowledge      # 바뀐 묶음만 다시 임베딩된다
```

사내 가이드는 `docs/<묶음>/*.md` 에 머리말(`id`, `title`, `keys`)과 함께 추가하면 됩니다.

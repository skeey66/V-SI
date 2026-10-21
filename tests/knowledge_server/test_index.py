"""지식 인덱스 시험. 가짜 임베더(핵심어 빈도 벡터)로 OpenAI 없이 돈다."""
from __future__ import annotations

from knowledge_server.index import (
    CHUNK_CHARS, COLLECTIONS, Doc, GuideIndex, chunk_doc, load_collection, load_docs, DOCS_DIR,
)
from lg_agent.knowledge import bandit_query

KEYS = ["shell", "sql", "eval", "random", "hash", "md5", "pickle", "password", "tmp", "yaml",
        "명령", "주입", "난수", "해시", "비밀번호", "임시", "경계", "이상", "이메일", "import", "모듈"]


def fake_embed(texts):
    return [[float(t.lower().count(k)) + 0.01 for k in KEYS] for t in texts]


def test_every_collection_has_curated_and_collected_docs():
    for name in COLLECTIONS:
        docs = load_collection(name)
        sources = {d.source for d in docs}
        assert "V-SI 사내 가이드" in sources, name           # 직접 쓴 가이드
        assert any(s.startswith("http") for s in sources), name  # 수집한 공개 문서


def test_curated_security_docs_use_rule_id_as_key():
    docs = load_docs(DOCS_DIR / "security")
    b602 = next(d for d in docs if d.id == "B602")
    assert b602.keys == ("B602",) and "shell" in b602.title


def test_long_docs_are_chunked_with_section_titles():
    body = "\n\n".join(f"문단 {i} " + "가" * 300 for i in range(12))
    chunks = chunk_doc(Doc(id="x", title="긴 문서", text=f"## 첫 절\n\n{body}\n\n## 둘째 절\n\n둘째 절의 본문이다. 너무 짧은 절은 검색에 쓸모가 없어 버리므로, 이 절은 마흔 자를 넉넉히 넘도록 적는다."))
    assert len(chunks) > 3
    assert all(len(c.text) <= CHUNK_CHARS * 1.5 for c in chunks)
    assert {c.section for c in chunks} == {"첫 절", "둘째 절"}


def test_key_match_beats_semantic_neighbours(tmp_path):
    index = GuideIndex(load_collection("security"), fake_embed, cache_dir=tmp_path, name="security")
    hits = index.search("B608 Possible SQL injection vector", k=3)
    assert hits[0]["id"] in ("B608", "bandit-B608")
    assert {"source", "section", "text"} <= set(hits[0])


def test_one_chunk_per_document(tmp_path):
    index = GuideIndex(load_collection("securecoding"), fake_embed, cache_dir=tmp_path, name="sc")
    hits = index.search("비밀번호 해시 저장 password hash", k=5, min_score=0.0)
    assert len({h["id"] for h in hits}) == len(hits)


def test_semantic_search_finds_test_design_for_threshold_requirement(tmp_path):
    index = GuideIndex(load_collection("testdesign"), fake_embed, cache_dir=tmp_path, name="td")
    hits = index.search("합계가 5만원 이상이면 배송비를 빼 주세요 (이상 경계)", k=3, min_score=0.0)
    assert any(h["id"] == "T01" for h in hits)


def test_embeddings_are_cached(tmp_path):
    calls = []

    def counting(texts):
        calls.append(len(texts))
        return fake_embed(texts)

    docs = load_collection("testdesign")
    GuideIndex(docs, counting, cache_dir=tmp_path, name="td")
    GuideIndex(docs, counting, cache_dir=tmp_path, name="td")
    assert len(calls) == 1  # 두 번째는 디스크 캐시에서 읽는다


def test_bandit_query_extracts_unique_issues():
    detail = (
        ">> Issue: [B602:subprocess_popen_with_shell_equals_true] subprocess call with shell=True identified\n"
        "   Location: ./count.py:5:4\n"
        ">> Issue: [B602:subprocess_popen_with_shell_equals_true] subprocess call with shell=True identified\n"
        ">> Issue: [B404:blacklist] Consider possible security implications associated with subprocess\n"
    )
    assert bandit_query(detail).splitlines() == [
        "B602 subprocess call with shell=True identified",
        "B404 Consider possible security implications associated with subprocess",
    ]


def test_short_doc_stays_whole_so_fix_is_not_cut_off():
    q01 = next(d for d in load_collection("qa") if d.id == "Q01")
    chunks = chunk_doc(q01)
    assert len(chunks) == 1
    assert "증상" in chunks[0].text and "고치는 방법" in chunks[0].text


def test_identifier_keys_boost_more_than_plain_words():
    from knowledge_server.index import _is_identifier

    for key in ("B602", "CWE-78", "ModuleNotFoundError", "UnicodeDecodeError"):
        assert _is_identifier(key), key
    for key in ("한글", "이상", "비밀번호", "assert"):
        assert not _is_identifier(key), key

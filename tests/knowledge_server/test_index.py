"""가이드 인덱스 시험. 가짜 임베더(글자 빈도 벡터)로 OpenAI 없이 돈다."""
from __future__ import annotations

from knowledge_server.index import GuideIndex, load_docs
from lg_agent.knowledge import bandit_query


def fake_embed(texts):
    keys = ["shell", "sql", "eval", "random", "hash", "md5", "pickle", "password", "tmp", "yaml",
            "명령", "주입", "난수", "해시", "비밀번호", "임시"]
    return [[float(t.lower().count(k)) + 0.01 for k in keys] for t in texts]


def test_docs_load_with_titles():
    docs = load_docs()
    assert len(docs) >= 20
    assert any(d.id == "B602" and "shell" in d.title for d in docs)


def test_rule_id_in_query_is_exact_hit(tmp_path):
    index = GuideIndex(load_docs(), fake_embed, cache_dir=tmp_path)
    hits = index.search("B608 Possible SQL injection vector", k=3)
    assert hits[0]["id"] == "B608" and hits[0]["score"] == 1.0


def test_semantic_search_without_rule_id(tmp_path):
    index = GuideIndex(load_docs(), fake_embed, cache_dir=tmp_path)
    hits = index.search("토큰 생성에 random 난수를 썼다", k=1, min_score=0.0)
    assert hits[0]["id"] == "B311"


def test_embeddings_are_cached(tmp_path):
    calls = []

    def counting(texts):
        calls.append(len(texts))
        return fake_embed(texts)

    GuideIndex(load_docs(), counting, cache_dir=tmp_path)
    GuideIndex(load_docs(), counting, cache_dir=tmp_path)
    assert len(calls) == 1  # 두 번째는 디스크 캐시에서 읽는다


def test_bandit_query_extracts_unique_issues():
    detail = (
        ">> Issue: [B602:subprocess_popen_with_shell_equals_true] subprocess call with shell=True identified\n"
        "   Location: ./count.py:5:4\n"
        ">> Issue: [B602:subprocess_popen_with_shell_equals_true] subprocess call with shell=True identified\n"
        ">> Issue: [B404:blacklist] Consider possible security implications associated with subprocess\n"
    )
    q = bandit_query(detail)
    assert q.splitlines() == [
        "B602 subprocess call with shell=True identified",
        "B404 Consider possible security implications associated with subprocess",
    ]

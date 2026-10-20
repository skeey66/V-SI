"""보안 조치 가이드 벡터 인덱스 (FAISS).

문서는 `docs/security/B*.md` 하나가 bandit 규칙 하나다. 문서 전체를 한 청크로
임베딩한다 — 문서가 짧고(300자 안팎) 한 문서가 한 주제라 쪼갤 이유가 없다.

임베딩은 비용이 든다. 그래서 문서 내용의 해시를 키로 인덱스를 디스크에 캐시하고,
문서가 바뀌었을 때만 다시 만든다. 25개 문서 전체 임베딩이 약 5천 토큰
(`text-embedding-3-small` 기준 0.0001달러)이다.

검색은 두 단계다.
1. 질의에 규칙 ID(`B602`)가 들어 있으면 그 문서를 점수 1.0 으로 먼저 넣는다.
   bandit 출력은 규칙 ID 를 정확히 주므로 이것이 가장 정확하다.
2. 나머지는 코사인 유사도(정규화 벡터의 내적)로 채우되 `min_score` 미만은 버린다.
   관련 없는 문서가 프롬프트에 섞이면 오히려 개발 에이전트를 헷갈리게 한다.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np

DOCS_DIR = Path(__file__).parent / "docs" / "security"
RULE_ID = re.compile(r"\bB\d{3}\b")

#: 텍스트 목록 → 벡터 목록. 실행 시 OpenAI, 테스트에서는 가짜를 주입한다.
Embedder = Callable[[Sequence[str]], list[list[float]]]


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    text: str


def load_docs(directory: Path = DOCS_DIR) -> list[Doc]:
    docs = []
    for path in sorted(directory.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        title = re.search(r"^title:\s*(.+)$", raw, re.M)
        body = raw.split("---", 2)[-1].strip()
        docs.append(Doc(id=path.stem, title=title.group(1) if title else path.stem, text=body))
    return docs


def _normalize(vectors: list[list[float]]) -> np.ndarray:
    arr = np.asarray(vectors, dtype="float32")
    faiss.normalize_L2(arr)
    return arr


class GuideIndex:
    def __init__(self, docs: list[Doc], embed: Embedder, cache_dir: Path | None = None) -> None:
        self.docs = docs
        self._by_id = {d.id: d for d in docs}
        self._embed = embed
        vectors = self._load_or_build(cache_dir)
        self._index = faiss.IndexFlatIP(vectors.shape[1])
        self._index.add(vectors)

    def _load_or_build(self, cache_dir: Path | None) -> np.ndarray:
        digest = hashlib.sha256(
            json.dumps([(d.id, d.text) for d in self.docs]).encode()
        ).hexdigest()[:16]
        cache = cache_dir / f"guides-{digest}.npy" if cache_dir else None
        if cache and cache.exists():
            return np.load(cache)
        vectors = _normalize(self._embed([f"{d.title}\n{d.text}" for d in self.docs]))
        if cache:
            cache_dir.mkdir(parents=True, exist_ok=True)
            np.save(cache, vectors)
        return vectors

    def get(self, doc_id: str) -> Doc | None:
        return self._by_id.get(doc_id)

    def search(self, query: str, k: int = 3, min_score: float = 0.35) -> list[dict]:
        hits: dict[str, float] = {}
        for rule in RULE_ID.findall(query):
            if rule in self._by_id:
                hits[rule] = 1.0
        if len(hits) < k:
            qv = _normalize(self._embed([query]))
            scores, idx = self._index.search(qv, min(k * 2, len(self.docs)))
            for score, i in zip(scores[0], idx[0]):
                doc_id = self.docs[i].id
                if score >= min_score and doc_id not in hits:
                    hits[doc_id] = float(score)
        ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [
            {"id": d, "title": self._by_id[d].title, "score": round(s, 3), "text": self._by_id[d].text}
            for d, s in ranked
        ]


def openai_embedder(model: str = "text-embedding-3-small") -> Embedder:
    from langchain_openai import OpenAIEmbeddings

    client = OpenAIEmbeddings(model=model)
    return lambda texts: client.embed_documents(list(texts))

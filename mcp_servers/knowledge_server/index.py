"""지식 벡터 인덱스 (FAISS) — 묶음 네 개.

| 묶음 | 쓰는 곳 | 사내 가이드 (`docs/`) | 수집 문서 (`corpus/`) |
|---|---|---|---|
| security     | 보안 · FAIL 후     | bandit 규칙별 조치 25 | bandit 공식 규칙 설명, MITRE CWE |
| securecoding | 개발 · 구현 전     | 시큐어 코딩 11        | OWASP Cheat Sheet, Python 보안 문서 |
| qa           | QA · FAIL 후       | pytest 실패 진단 15   | pytest 문서, Python 내장 예외 |
| testdesign   | 기획 · 테스트 작성 전 | 테스트 설계 15     | pytest parametrize · raises · tmp_path |

**청크.** 청크 하나 크기보다 짧은 문서는 통째로 둔다. 긴 문서는 `## 제목` 단위로 자르고, 긴 절은 문단 경계에서 최대 `CHUNK_CHARS` 자로
다시 나눈다(앞 청크 끝 `OVERLAP` 자를 겹친다). 사내 가이드는 짧아서 대개 한 청크다.

**검색.** 정확 매칭과 의미 검색을 섞는다.
1. 문서 머리말 `keys` 가 질의에 들어 있으면 가산한다. 규칙 ID · CWE · 오류 이름 같은
   식별자는 `KEY_BOOST`(bandit·pytest 출력이 정확히 주므로 가장 믿을 만한 신호),
   한국어 일반 단어는 `WORD_KEY_BOOST` 만 — 우연히 겹치기 쉬워서다.
2. 코사인 유사도(정규화 벡터의 내적)로 순위를 매기고 `min_score` 미만은 버린다.
   관련 없는 문서가 프롬프트에 섞이면 오히려 에이전트를 헷갈리게 한다.
3. 같은 문서에서는 청크를 하나만 고른다 — 결과가 한 문서로 쏠리지 않게.

**임베딩 비용.** 묶음별로 청크 내용의 해시를 키로 벡터를 디스크에 캐시한다. 문서가
바뀔 때만 다시 임베딩한다 (전체 약 20만 토큰, `text-embedding-3-small` 로 약 $0.004).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import faiss
import numpy as np

BASE = Path(__file__).parent
DOCS_DIR = BASE / "docs"
CORPUS_DIR = BASE / "corpus"
COLLECTIONS = ("security", "securecoding", "qa", "testdesign")

CHUNK_CHARS = 1200
OVERLAP = 150
#: 식별자 키(규칙 ID · CWE · 오류 이름)는 강하게, 한국어 일반 단어 키는 약하게 가산한다.
#: 실측: '한글' 같은 일반 단어 키에 강한 가산을 주자 상관없는 인코딩 가이드가 1위로 올라왔다.
KEY_BOOST = 0.35
WORD_KEY_BOOST = 0.08
RULE_ID = re.compile(r"\bB\d{3}\b")

#: 텍스트 목록 → 벡터 목록. 실행 시 OpenAI, 테스트에서는 가짜를 주입한다.
Embedder = Callable[[Sequence[str]], list[list[float]]]


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    text: str
    source: str = "V-SI 사내 가이드"
    keys: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Chunk:
    doc: Doc
    section: str
    text: str

    @property
    def embed_text(self) -> str:
        return f"{self.doc.title} › {self.section}\n{self.text}"


def _front_matter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    _, head, body = raw.split("---", 2)
    meta = {}
    for line in head.strip().splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta, body.strip()


def load_docs(directory: Path) -> list[Doc]:
    docs = []
    for path in sorted(directory.glob("*.md")):
        meta, body = _front_matter(path.read_text(encoding="utf-8"))
        keys = tuple(k.strip() for k in meta.get("keys", "").split(",") if k.strip())
        if RULE_ID.fullmatch(path.stem):  # 사내 보안 가이드는 파일 이름이 규칙 ID 다
            keys = keys or (path.stem,)
        docs.append(Doc(
            id=meta.get("id", path.stem), title=meta.get("title", path.stem), text=body,
            source=meta.get("source", "V-SI 사내 가이드"), keys=keys,
        ))
    return docs


def load_collection(name: str, docs_dir: Path = DOCS_DIR, corpus_dir: Path = CORPUS_DIR) -> list[Doc]:
    out = []
    for base in (docs_dir, corpus_dir):
        folder = base / name
        if folder.is_dir():
            out += load_docs(folder)
    return out


def chunk_doc(doc: Doc) -> list[Chunk]:
    # 짧은 문서(사내 가이드 대부분)는 통째로 한 청크 — 절로 쪼개면 "증상"만 검색되고
    # 바로 아래 "고치는 방법"이 빠지는 일이 생긴다 (실측).
    if len(doc.text) <= CHUNK_CHARS:
        return [Chunk(doc, "전체", doc.text.strip())] if len(doc.text.strip()) >= 40 else []
    parts = re.split(r"^##\s+(.+)$", doc.text, flags=re.M)
    sections = [("개요", parts[0])] + list(zip(parts[1::2], parts[2::2]))
    chunks = []
    for head, body in sections:
        body = body.strip()
        if len(body) < 40:
            continue
        paragraphs, buf = re.split(r"\n\s*\n", body), ""
        for para in paragraphs:
            if buf and len(buf) + len(para) > CHUNK_CHARS:
                chunks.append(Chunk(doc, head.strip(), buf.strip()))
                buf = buf[-OVERLAP:] + "\n\n"
            buf += para + "\n\n"
            while len(buf) > CHUNK_CHARS * 1.5:  # 문단 하나가 너무 길면 강제로 자른다
                chunks.append(Chunk(doc, head.strip(), buf[:CHUNK_CHARS].strip()))
                buf = buf[CHUNK_CHARS - OVERLAP:]
        if buf.strip():
            chunks.append(Chunk(doc, head.strip(), buf.strip()))
    return chunks


def _normalize(vectors: list[list[float]]) -> np.ndarray:
    arr = np.asarray(vectors, dtype="float32")
    faiss.normalize_L2(arr)
    return arr


def _is_identifier(key: str) -> bool:
    """규칙 ID(B602) · CWE-78 · 오류 이름(ModuleNotFoundError) 처럼 우연히 겹치기 어려운 키."""
    return bool(re.fullmatch(r"B\d{3}|CWE-\d+|[A-Z][A-Za-z]+(Error|Exception)|[0-9.]{6,}", key))


def _key_hit(key: str, query: str) -> bool:
    if re.fullmatch(r"[A-Za-z0-9_\-.]+", key):  # 영문 식별자는 단어 경계로
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", query) is not None
    return key in query


class GuideIndex:
    """한 묶음의 문서를 청크로 나눠 FAISS 에 넣는다."""

    def __init__(self, docs: list[Doc], embed: Embedder, cache_dir: Path | None = None,
                 name: str = "guides") -> None:
        self.docs = docs
        self._by_id = {d.id: d for d in docs}
        self.chunks = [c for d in docs for c in chunk_doc(d)]
        self._embed = embed
        vectors = self._load_or_build(cache_dir, name)
        self._index = faiss.IndexFlatIP(vectors.shape[1])
        self._index.add(vectors)

    def _load_or_build(self, cache_dir: Path | None, name: str) -> np.ndarray:
        digest = hashlib.sha256(json.dumps([c.embed_text for c in self.chunks]).encode()).hexdigest()[:16]
        cache = cache_dir / f"{name}-{digest}.npy" if cache_dir else None
        if cache and cache.exists():
            return np.load(cache)
        vectors = _normalize(self._embed([c.embed_text for c in self.chunks]))
        if cache:
            cache_dir.mkdir(parents=True, exist_ok=True)
            np.save(cache, vectors)
        return vectors

    def get(self, doc_id: str) -> Doc | None:
        return self._by_id.get(doc_id)

    def search(self, query: str, k: int = 3, min_score: float = 0.35) -> list[dict]:
        qv = _normalize(self._embed([query]))
        n = min(len(self.chunks), max(k * 8, 40))
        scores, idx = self._index.search(qv, n)
        boost: dict[str, float] = {}
        for d in self.docs:
            hit = [k for k in d.keys if _key_hit(k, query)]
            if hit:
                boost[d.id] = KEY_BOOST if any(_is_identifier(k) for k in hit) else WORD_KEY_BOOST
        boosted = set(boost)
        # 키가 맞은 문서는 검색 상위에 없더라도 후보에 넣는다
        candidates = {int(i): float(s) for s, i in zip(scores[0], idx[0])}
        if boosted:
            all_scores = self._index.reconstruct_n(0, len(self.chunks)) @ qv[0]
            for i, c in enumerate(self.chunks):
                if c.doc.id in boosted:
                    candidates[i] = float(all_scores[i])
        ranked = sorted(
            ((s + boost.get(self.chunks[i].doc.id, 0.0), i) for i, s in candidates.items()),
            reverse=True,
        )
        hits, seen = [], set()
        for score, i in ranked:
            c = self.chunks[i]
            if score < min_score or c.doc.id in seen:
                continue
            seen.add(c.doc.id)
            hits.append({
                "id": c.doc.id, "title": c.doc.title, "section": c.section,
                "score": round(min(score, 1.0), 3), "source": c.doc.source, "text": c.text,
            })
            if len(hits) == k:
                break
        return hits


def build_indexes(embed: Embedder, cache_dir: Path | None = None) -> dict[str, GuideIndex]:
    return {name: GuideIndex(load_collection(name), embed, cache_dir, name) for name in COLLECTIONS}


def openai_embedder(model: str = "text-embedding-3-small") -> Embedder:
    from langchain_openai import OpenAIEmbeddings

    client = OpenAIEmbeddings(model=model, chunk_size=256)
    return lambda texts: client.embed_documents(list(texts))

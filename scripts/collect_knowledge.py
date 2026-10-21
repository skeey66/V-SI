"""RAG 지식 코퍼스 수집기.

    PYTHONPATH=backend python scripts/collect_knowledge.py

공개 문서를 내려받아 `mcp_servers/knowledge_server/corpus/<묶음>/` 에 마크다운으로 저장한다.
각 파일 머리에 출처 URL 과 라이선스를 남기고, 전체 목록은 `corpus/SOURCES.md` 에 쓴다.
직접 쓴 사내 가이드(`docs/<묶음>/`)와 함께 지식 서버가 인덱싱한다.

| 묶음 | 수집 출처 |
|---|---|
| security     | bandit 공식 플러그인·블랙리스트 문서(설치된 패키지에서 추출), MITRE CWE |
| securecoding | OWASP Cheat Sheet Series, Python 공식 문서의 보안 관련 절 |
| qa           | pytest 공식 문서, Python 내장 예외 문서 |
| testdesign   | pytest 공식 문서 (parametrize · raises · approx · tmp_path · 테스트 구조) |

네트워크가 필요하다. 다시 실행하면 덮어쓴다(결과는 결정적이다 — 같은 원문이면 같은 파일).
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1] / "mcp_servers" / "knowledge_server" / "corpus"

OWASP = "https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/{}.md"
PYTEST = "https://raw.githubusercontent.com/pytest-dev/pytest/main/doc/en/{}.rst"
CPYTHON = "https://raw.githubusercontent.com/python/cpython/3.11/Doc/library/{}.rst"
CWE_API = "https://cwe-api.mitre.org/api/v1/cwe/weakness/{}"

LICENSE = {
    "owasp": "CC BY-SA 4.0 (OWASP Cheat Sheet Series)",
    "pytest": "MIT (pytest-dev)",
    "cpython": "PSF License (Python documentation)",
    "bandit": "Apache-2.0 (PyCQA bandit)",
    "cwe": "MITRE CWE Terms of Use",
}

# (묶음, 출처 종류, 이름, 제목, 남길 절을 고르는 정규식 — 없으면 전부)
SOURCES: list[tuple[str, str, str, str, str | None]] = [
    # 시큐어 코딩 — 개발이 구현 전에 찾는다
    ("securecoding", "owasp", "Password_Storage_Cheat_Sheet", "OWASP 비밀번호 저장", None),
    ("securecoding", "owasp", "SQL_Injection_Prevention_Cheat_Sheet", "OWASP SQL 주입 방지", None),
    ("securecoding", "owasp", "Query_Parameterization_Cheat_Sheet", "OWASP 쿼리 파라미터화", None),
    ("securecoding", "owasp", "OS_Command_Injection_Defense_Cheat_Sheet", "OWASP OS 명령 주입 방어", None),
    ("securecoding", "owasp", "Input_Validation_Cheat_Sheet", "OWASP 입력 검증", None),
    ("securecoding", "owasp", "File_Upload_Cheat_Sheet", "OWASP 파일 업로드", None),
    ("securecoding", "owasp", "Cryptographic_Storage_Cheat_Sheet", "OWASP 암호화 저장", None),
    ("securecoding", "owasp", "Deserialization_Cheat_Sheet", "OWASP 역직렬화", r"(?i)python|general|guidance|introduction"),
    ("securecoding", "owasp", "Secrets_Management_Cheat_Sheet", "OWASP 비밀값 관리", r"(?i)introduction|general|hardcod|environment|code"),
    ("securecoding", "owasp", "Error_Handling_Cheat_Sheet", "OWASP 오류 처리", None),
    ("securecoding", "cpython", "secrets", "Python secrets 모듈", None),
    ("securecoding", "cpython", "hashlib", "Python hashlib 모듈", r"(?i)key deriv|pbkdf2|scrypt|usedforsecurity|hash algorithms|blake2"),
    ("securecoding", "cpython", "hmac", "Python hmac 모듈", r"(?i)compare_digest|hmac"),
    ("securecoding", "cpython", "tempfile", "Python tempfile 모듈", r"(?i)mkstemp|namedtemporaryfile|mktemp|deprecated|example"),
    ("securecoding", "cpython", "subprocess", "Python subprocess 모듈", r"(?i)security considerations|shell=true|shell injection|shlex"),
    ("securecoding", "cpython", "ast", "Python ast 모듈", r"(?i)literal_eval"),
    ("securecoding", "cpython", "pickle", "Python pickle 모듈", r"(?i)warning|security|restricting globals"),
    # 테스트 실패 진단 — QA 가 FAIL 후 찾는다
    ("qa", "pytest", "how-to/assert", "pytest 단언과 실패 메시지", None),
    ("qa", "pytest", "explanation/goodpractices", "pytest 테스트 배치와 import", None),
    ("qa", "pytest", "explanation/pythonpath", "pytest import 경로", None),
    ("qa", "pytest", "how-to/output", "pytest 출력 읽기", r"(?i)traceback|summary|verbosity|-r"),
    ("qa", "cpython", "exceptions", "Python 내장 예외", None),
    # 테스트 설계 — 기획이 테스트를 쓰기 전에 찾는다
    ("testdesign", "pytest", "how-to/parametrize", "pytest parametrize", None),
    ("testdesign", "pytest", "how-to/tmp_path", "pytest 임시 경로", None),
    ("testdesign", "pytest", "explanation/anatomy", "테스트의 구조 (Arrange-Act-Assert)", None),
    ("testdesign", "pytest", "how-to/assert", "pytest 예외·근사값 검사", r"(?i)raises|approx|exception|warning"),
]

#: 다른 언어·플랫폼 전용 절 — 이 서비스는 파이썬 코드만 만들므로 검색 잡음이다 (실측: Rust 예제가 검색됨)
OTHER_LANG = re.compile(
    r"(?i)\b(java|c#|\.net|php|ruby|rust|golang|node(\.js)?|javascript|typescript|perl|kotlin|swift|"
    r"coldfusion|vb\.net|asp|hibernate|spring|oracle|mysql|sql server|db2|postgres|android|ios)\b")

# bandit 규칙에서 CWE 로 이어지는 항목 + 시큐어 코딩에 자주 걸리는 약점
CWE_IDS = [20, 22, 78, 79, 89, 94, 259, 295, 327, 330, 377, 400, 434, 502, 605, 611, 703, 732, 798, 918, 1333]


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()


def clean_rst(text: str) -> str:
    """RST 표기 중 읽기를 방해하는 것만 지운다 (코드는 남긴다)."""
    text = re.sub(r":[a-z:]+:`~?([^`<]+?)(?:\s*<[^>]+>)?`", r"`\1`", text)  # :func:`x <y>` → `x`
    text = re.sub(r"^\.\. (index|versionadded|versionchanged|deprecated|seealso|module|currentmodule|_[^:]+):.*$",
                  "", text, flags=re.M)
    text = re.sub(r"^\.\. (code-block|sourcecode|testcode|doctest)::.*$", "```", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text)


def sections(text: str, markdown: bool) -> list[tuple[str, str]]:
    """제목 단위로 자른다. RST 는 '제목 다음 줄이 같은 길이의 밑줄' 형식."""
    lines = text.splitlines()
    out, head, buf = [], "개요", []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        is_md = markdown and re.match(r"^#{1,4}\s+\S", line)
        is_rst = (not markdown) and line.strip() and re.fullmatch(r"([=\-~^\"'*+#])\1{2,}", nxt.strip() or "x") \
            and len(nxt.strip()) >= len(line.strip()) - 1
        if is_md or is_rst:
            if "".join(buf).strip():
                out.append((head, "\n".join(buf).strip()))
            head, buf = line.lstrip("#").strip(), []
            i += 2 if is_rst else 1
            continue
        if not markdown and re.fullmatch(r"([=\-~^\"'*+#])\1{2,}", line.strip() or "x") and not buf:
            i += 1  # 제목 위에 붙는 윗줄
            continue
        buf.append(line)
        i += 1
    if "".join(buf).strip():
        out.append((head, "\n".join(buf).strip()))
    return out


def write_doc(collection: str, doc_id: str, title: str, url: str, license_: str, keys: list[str], body: str) -> None:
    folder = ROOT / collection
    folder.mkdir(parents=True, exist_ok=True)
    front = (f"---\nid: {doc_id}\ntitle: {title}\nsource: {url}\nlicense: {license_}\n"
             f"keys: {', '.join(keys)}\n---\n")
    (folder / f"{doc_id}.md").write_text(front + body.strip() + "\n", encoding="utf-8")


def collect_text_sources(http: httpx.Client) -> list[tuple[str, str, str]]:
    done = []
    for collection, kind, name, title, keep in SOURCES:
        url = {"owasp": OWASP, "pytest": PYTEST, "cpython": CPYTHON}[kind].format(name)
        resp = http.get(url)
        resp.raise_for_status()
        markdown = kind == "owasp"
        text = resp.text if markdown else clean_rst(resp.text)
        parts = sections(text, markdown)
        if keep:
            parts = [(h, b) for h, b in parts if re.search(keep, h) or re.search(keep, b)]
        if kind == "owasp":
            parts = [(h, b) for h, b in parts if not OTHER_LANG.search(h)]
        body = "\n\n".join(f"## {h}\n\n{b}" for h, b in parts)
        doc_id = f"{kind}-{slug(name)}" + ("" if collection != "testdesign" or kind != "pytest" or name != "how-to/assert" else "-raises")
        write_doc(collection, doc_id, title, url, LICENSE[kind], [], body)
        done.append((collection, title, url))
        print(f"  {collection:12} {len(body):>7}자  {title}")
    return done


def collect_bandit() -> list[tuple[str, str, str]]:
    """설치된 bandit 패키지에서 규칙별 공식 설명을 뽑는다 (네트워크 불필요)."""
    import bandit
    from bandit.blacklists import calls, imports
    from bandit.core import extension_loader

    manager = extension_loader.MANAGER
    url = f"https://bandit.readthedocs.io/en/{bandit.__version__}/plugins/index.html"
    done = []
    for p in manager.plugins:
        test_id = getattr(p.plugin, "_test_id", None)
        doc = inspect.cleandoc(p.plugin.__doc__ or "")
        if not test_id or not doc:
            continue
        write_doc("security", f"bandit-{test_id}", f"bandit {test_id} {p.name}", url, LICENSE["bandit"],
                  [test_id], clean_rst(doc))
        done.append(("security", f"bandit {test_id}", url))
    module_docs = clean_rst((calls.__doc__ or "") + "\n" + (imports.__doc__ or ""))
    for test_id, item in manager.blacklist_by_id.items():
        # 모듈 문서에서 그 ID 의 절만 잘라 온다 (예: "B301: pickle")
        m = re.search(rf"{test_id}: .*?(?=\nB\d{{3}}: |\Z)", module_docs, flags=re.S)
        body = (f"{item['message']}\n\n대상 호출: {', '.join(item['qualnames'])}\n"
                f"심각도: {item.get('level', '-')} · CWE-{item.get('cwe', '-')}\n\n" + (m.group(0) if m else ""))
        write_doc("security", f"bandit-{test_id}", f"bandit {test_id} {item['name']}", url, LICENSE["bandit"],
                  [test_id, f"CWE-{item.get('cwe')}"], body)
        done.append(("security", f"bandit {test_id}", url))
    print(f"  security     bandit 규칙 {len(done)}개")
    return done


def collect_cwe(http: httpx.Client) -> list[tuple[str, str, str]]:
    done = []
    for cwe in CWE_IDS:
        resp = http.get(CWE_API.format(cwe))
        if resp.status_code != 200:
            print(f"  CWE-{cwe} 건너뜀 (HTTP {resp.status_code})")
            continue
        w = resp.json()["Weaknesses"][0]
        mitigations = "\n".join(
            f"- [{', '.join(m.get('Phase', []))}] {m.get('Description', '').strip()}"
            for m in (w.get("PotentialMitigations") or []))
        consequences = "\n".join(
            f"- {', '.join(c.get('Scope', []))}: {', '.join(c.get('Impact', []))}"
            for c in (w.get("CommonConsequences") or []))
        body = (f"## 설명\n\n{w.get('Description', '')}\n\n{w.get('ExtendedDescription', '') or ''}\n\n"
                f"## 영향\n\n{consequences}\n\n## 완화 방법\n\n{mitigations}")
        url = f"https://cwe.mitre.org/data/definitions/{cwe}.html"
        write_doc("security", f"cwe-{cwe}", f"CWE-{cwe} {w['Name']}", url, LICENSE["cwe"], [f"CWE-{cwe}"], body)
        done.append(("security", f"CWE-{cwe}", url))
    print(f"  security     CWE {len(done)}개")
    return done


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30, follow_redirects=True) as http:
        rows = collect_text_sources(http) + collect_bandit() + collect_cwe(http)
    lines = ["# 수집 문서 출처\n", "`scripts/collect_knowledge.py` 가 내려받은 문서와 라이선스.\n",
             "| 묶음 | 문서 | 출처 |", "|---|---|---|"]
    lines += [f"| {c} | {t} | {u} |" for c, t, u in rows]
    lines += ["", "라이선스: " + " · ".join(LICENSE.values())]
    (ROOT / "SOURCES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"총 {len(rows)}개 → {ROOT}")


if __name__ == "__main__":
    sys.exit(main())

import { describe, expect, it } from "vitest";
import { crc32, makeZip } from "./zip";

const u32 = (b: Uint8Array, o: number) => new DataView(b.buffer).getUint32(o, true);
const u16 = (b: Uint8Array, o: number) => new DataView(b.buffer).getUint16(o, true);

describe("crc32", () => {
  it("알려진 값과 맞는다", () => {
    // 규격 검증용 고정값 — 이게 틀리면 압축 해제 도구가 "손상됨"이라고 한다.
    expect(crc32(new TextEncoder().encode("hello"))).toBe(0x3610a686);
    expect(crc32(new Uint8Array(0))).toBe(0);
  });
});

describe("makeZip", () => {
  const now = new Date(2026, 8, 21, 16, 40, 20);

  it("로컬 헤더 · 중앙 디렉터리 · 끝 표식이 다 있다", () => {
    const z = makeZip({ "a.py": "x = 1\n" }, now);
    expect(u32(z, 0)).toBe(0x04034b50); // 로컬 헤더
    const eocd = z.length - 22;
    expect(u32(z, eocd)).toBe(0x06054b50); // 끝 표식
    expect(u16(z, eocd + 8)).toBe(1); // 파일 1개
    const cdOffset = u32(z, eocd + 16);
    expect(u32(z, cdOffset)).toBe(0x02014b50); // 중앙 디렉터리
  });

  it("중앙 디렉터리가 가리키는 위치에 실제 로컬 헤더가 있다", () => {
    // 오프셋 계산이 틀리면 도구가 파일을 못 찾는다 — 가장 흔한 버그다.
    const z = makeZip({ "a.py": "1", "b/c.md": "# hi" }, now);
    const eocd = z.length - 22;
    let p = u32(z, eocd + 16);
    for (let i = 0; i < 2; i += 1) {
      expect(u32(z, p)).toBe(0x02014b50);
      const nameLen = u16(z, p + 28);
      const localAt = u32(z, p + 42);
      expect(u32(z, localAt)).toBe(0x04034b50);
      p += 46 + nameLen;
    }
  });

  it("압축하지 않는다 — 크기 두 값이 같고 방식이 0 이다", () => {
    const body = "def add(a, b):\n    return a + b\n";
    const z = makeZip({ "add.py": body }, now);
    const size = new TextEncoder().encode(body).length;
    expect(u16(z, 8)).toBe(0); // STORE
    expect(u32(z, 18)).toBe(size); // 압축 후
    expect(u32(z, 22)).toBe(size); // 원본
  });

  it("내용이 그대로 들어간다", () => {
    const z = makeZip({ "a.py": "hello" }, now);
    const nameLen = u16(z, 26);
    const body = z.slice(30 + nameLen, 30 + nameLen + 5);
    expect(new TextDecoder().decode(body)).toBe("hello");
    expect(u32(z, 14)).toBe(crc32(new TextEncoder().encode("hello")));
  });

  it("한글·UTF-8 파일명에 플래그를 세운다", () => {
    // 세우지 않으면 압축 해제 도구가 CP437 로 읽어 이름이 깨진다.
    const z = makeZip({ "명세.md": "내용" }, now);
    expect(u16(z, 6) & 0x0800).toBe(0x0800);
  });

  it("빈 목록도 열리는 ZIP 을 만든다", () => {
    const z = makeZip({}, now);
    expect(z.length).toBe(22);
    expect(u32(z, 0)).toBe(0x06054b50);
    expect(u16(z, 8)).toBe(0);
  });
});

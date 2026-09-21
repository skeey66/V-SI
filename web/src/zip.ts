/** 의존성 없는 ZIP 만들기 (STORE, 압축 안 함).
 *
 * 산출물은 이미 화면에 다 와 있다(`artifacts` API 로 받는다). 그러니 받기
 * 위해 백엔드를 더 만들 이유가 없다 — 브라우저에서 묶어서 저장하면 된다.
 *
 * 압축하지 않는 이유: 산출물은 소스 파일 몇 개이고 상한이 200KB 다
 * (`llm_agent.loop.FILE_SNAPSHOT_TOTAL_CAP_BYTES`). 압축을 넣으려면
 * DEFLATE 구현이나 라이브러리가 필요한데, 그 대가로 얻는 것이 몇십 KB 다.
 * STORE 는 규격상 완전히 정상이고 어떤 압축 해제 도구로도 열린다.
 */

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let c = i;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[i] = c >>> 0;
  }
  return t;
})();

export function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** DOS 형식 날짜·시각. 초는 2초 단위로만 담긴다(규격이 그렇다). */
function dosTime(d: Date): { time: number; date: number } {
  return {
    time: (d.getHours() << 11) | (d.getMinutes() << 5) | (Math.floor(d.getSeconds() / 2) & 0x1f),
    date: ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate(),
  };
}

/** 파일 이름 → 내용(UTF-8 문자열)을 ZIP 바이트로. */
export function makeZip(files: Record<string, string>, now: Date = new Date()): Uint8Array {
  const enc = new TextEncoder();
  const { time, date } = dosTime(now);
  const locals: Uint8Array[] = [];
  const centrals: Uint8Array[] = [];
  let offset = 0;

  for (const [name, content] of Object.entries(files)) {
    const nameBytes = enc.encode(name);
    const body = enc.encode(content);
    const crc = crc32(body);

    const local = new Uint8Array(30 + nameBytes.length + body.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true); // 로컬 헤더 표식
    lv.setUint16(4, 20, true); // 필요 버전
    lv.setUint16(6, 0x0800, true); // 이름이 UTF-8 이라는 표시 — 한글 파일명 대비
    lv.setUint16(8, 0, true); // 압축 방식 0 = STORE
    lv.setUint16(10, time, true);
    lv.setUint16(12, date, true);
    lv.setUint32(14, crc, true);
    lv.setUint32(18, body.length, true);
    lv.setUint32(22, body.length, true);
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true);
    local.set(nameBytes, 30);
    local.set(body, 30 + nameBytes.length);
    locals.push(local);

    const central = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(central.buffer);
    cv.setUint32(0, 0x02014b50, true); // 중앙 디렉터리 표식
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0x0800, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, time, true);
    cv.setUint16(14, date, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, body.length, true);
    cv.setUint32(24, body.length, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint32(42, offset, true); // 이 파일의 로컬 헤더 위치
    central.set(nameBytes, 46);
    centrals.push(central);

    offset += local.length;
  }

  const centralSize = centrals.reduce((n, c) => n + c.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true); // 끝 표식
  ev.setUint16(8, centrals.length, true);
  ev.setUint16(10, centrals.length, true);
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, offset, true); // 중앙 디렉터리 시작 위치

  const total = offset + centralSize + end.length;
  const out = new Uint8Array(total);
  let p = 0;
  for (const b of [...locals, ...centrals, end]) {
    out.set(b, p);
    p += b.length;
  }
  return out;
}

/** 브라우저에서 파일로 저장한다. */
export function downloadZip(fileName: string, files: Record<string, string>): void {
  // `Uint8Array<ArrayBufferLike>` 를 그대로 넘기면 TS 가 SharedArrayBuffer 가능성
  // 때문에 거부한다. 실제로는 항상 ArrayBuffer 다 — 바이트를 그대로 복사해 넘긴다.
  const bytes = makeZip(files);
  const buf = new ArrayBuffer(bytes.length);
  new Uint8Array(buf).set(bytes);
  const blob = new Blob([buf], { type: "application/zip" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

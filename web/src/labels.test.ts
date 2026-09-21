import { describe, expect, it } from "vitest";
import { STEPS, ago, elapsed, splitBlocks, stateTone, stepIndex } from "./labels";

describe("상태 표기", () => {
  it("완료/확인/도움을 서로 다른 색으로 가른다", () => {
    expect(stateTone("accepted")).toBe("good");
    expect(stateTone("blocked")).toBe("warn");
    expect(stateTone("escalated")).toBe("bad");
    expect(stateTone("implementing")).toBe("busy");
  });

  it("blocked 는 기획 단계에 선다 — 기획 게이트가 그 자리이기 때문", () => {
    expect(stepIndex("blocked")).toBe(0);
    expect(stepIndex("implementing")).toBe(1);
    expect(stepIndex("verifying")).toBe(2);
    expect(stepIndex("accepted")).toBe(STEPS.length);
  });

  it("모르는 상태는 단계를 찍지 않는다 — 추측하지 않는다", () => {
    expect(stepIndex("escalated")).toBe(-1);
    expect(stepIndex("wat")).toBe(-1);
  });
});

describe("시간 표기", () => {
  const now = new Date("2026-09-21T10:00:00Z");
  it("상대 시간으로 옮긴다", () => {
    expect(ago("2026-09-21T09:59:30Z", now)).toBe("방금");
    expect(ago("2026-09-21T09:55:00Z", now)).toBe("5분 전");
    expect(ago("2026-09-21T07:00:00Z", now)).toBe("3시간 전");
  });
  it("깨진 값에 NaN 을 흘리지 않는다", () => {
    expect(ago(null)).toBe("");
    expect(ago("어제")).toBe("");
  });
  it("경과는 분·초로", () => {
    expect(elapsed(45)).toBe("45초");
    expect(elapsed(192)).toBe("3분 12초");
  });
});

describe("설명에서 코드블록 가르기", () => {
  it("```로 감싼 부분을 코드로 뗀다", () => {
    const out = splitBlocks("앞말\n```python\ndef add(a,b):\n    return a+b\n```\n뒷말");
    expect(out.map((b) => b.kind)).toEqual(["text", "code", "text"]);
    expect(out[1].body).toBe("def add(a,b):\n    return a+b");
    expect(out[2].body).toBe("뒷말");
  });

  it("코드블록이 없으면 글 하나다", () => {
    expect(splitBlocks("그냥 설명")).toEqual([{ kind: "text", body: "그냥 설명" }]);
  });

  it("닫히지 않은 백틱도 화면에 날것으로 새지 않는다", () => {
    // 실측: QA 보고서에 ``` 가 글자로 그대로 보였다.
    const out = splitBlocks("설명\n```\n4 passed");
    expect(out.every((b) => !b.body.includes("```"))).toBe(true);
  });

  it("언어 표시가 없는 코드블록도 딴다", () => {
    const out = splitBlocks("결과:\n```\n4 passed in 0.00s\n```");
    expect(out[1]).toEqual({ kind: "code", body: "4 passed in 0.00s" });
  });
});

import { describe, expect, it, vi } from "vitest";

const pretextMock = vi.hoisted(() => ({
  prepare: vi.fn(() => ({ prepared: true })),
  layout: vi.fn(() => ({ lineCount: 2, height: 44 })),
}));

vi.mock("@chenglou/pretext", () => pretextMock);

import {
  applyMeasuredTextareaHeight,
  measureTextareaHeightWithPretext,
} from "@/lib/pretextTextarea";

function makeTextarea(value: string, width = 240): HTMLTextAreaElement {
  const el = document.createElement("textarea");
  el.value = value;
  Object.defineProperty(el, "clientWidth", { configurable: true, value: width });
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: 88 });
  document.body.appendChild(el);
  return el;
}

describe("pretext textarea measurement", () => {
  it("estimates height without relying on scrollHeight when text wraps", () => {
    pretextMock.prepare.mockClear();
    pretextMock.layout.mockClear();
    const el = makeTextarea(
      "OriginAgent can use Pretext to estimate textarea wrapping without forcing DOM scrollHeight reads.",
      170,
    );
    el.style.font = '400 16px / 20px "Arial"';
    el.style.paddingTop = "8px";
    el.style.paddingRight = "12px";
    el.style.paddingBottom = "8px";
    el.style.paddingLeft = "12px";

    const height = measureTextareaHeightWithPretext(el, 260);

    expect(pretextMock.prepare).toHaveBeenCalledWith(
      el.value,
      expect.any(String),
      expect.objectContaining({ whiteSpace: "pre-wrap" }),
    );
    expect(pretextMock.layout).toHaveBeenCalledWith(
      expect.anything(),
      146,
      expect.any(Number),
    );
    expect(height).toBe(60);
  });

  it("falls back to scrollHeight when Intl.Segmenter is unavailable", () => {
    const el = makeTextarea("fallback please", 170);
    const segmenter = Intl.Segmenter;
    Object.defineProperty(Intl, "Segmenter", {
      configurable: true,
      value: undefined,
    });

    try {
      expect(measureTextareaHeightWithPretext(el, 260)).toBe(88);
    } finally {
      Object.defineProperty(Intl, "Segmenter", {
        configurable: true,
        value: segmenter,
      });
    }
  });

  it("applies the measured height inline", () => {
    const el = makeTextarea("hello\nworld", 240);
    el.style.font = '400 16px / 20px "Arial"';
    el.style.padding = "8px 12px";
    const spy = vi.spyOn(el.style, "height", "set");

    applyMeasuredTextareaHeight(el);

    expect(spy).toHaveBeenCalled();
    expect(el.style.height.endsWith("px")).toBe(true);
  });
});

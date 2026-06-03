import { describe, expect, it } from "vitest";

import type { DisplayUnit } from "@/components/thread/ThreadMessages";
import {
  buildVirtualDisplayUnits,
  pickVisibleUnits,
  type HeightCache,
} from "@/hooks/useThreadVirtualizer";

function unit(id: string): DisplayUnit {
  return {
    type: "single",
    message: {
      id,
      role: "assistant",
      content: id,
      createdAt: 1,
    },
  };
}

describe("thread virtualizer", () => {
  it("builds offsets using measured heights when available", () => {
    const units = [unit("a"), unit("b"), unit("c")];
    const heights: HeightCache = new Map([["b", 80]]);

    const virtual = buildVirtualDisplayUnits(units, heights, () => 40);

    expect(virtual.map((item) => [item.key, item.top, item.height])).toEqual([
      ["a", 0, 40],
      ["b", 40, 80],
      ["c", 120, 40],
    ]);
  });

  it("selects only the overscanned window", () => {
    const units = Array.from({ length: 20 }, (_, i) => unit(`m${i}`));
    const virtual = buildVirtualDisplayUnits(units, new Map(), () => 50);

    const visible = pickVisibleUnits(virtual, 400, 100, 50);

    expect(visible[0].key).toBe("m6");
    expect(visible.at(-1)?.key).toBe("m11");
  });

  it("can keep the last streaming unit rendered outside the current window", () => {
    const units = Array.from({ length: 20 }, (_, i) => unit(`m${i}`));
    const virtual = buildVirtualDisplayUnits(units, new Map(), () => 50);

    const visible = pickVisibleUnits(virtual, 0, 100, 0, true);

    expect(visible.map((item) => item.key)).toEqual(["m0", "m1", "m2", "m19"]);
  });
});

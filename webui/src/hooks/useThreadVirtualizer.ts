import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { unitKey, type DisplayUnit } from "@/components/thread/ThreadMessages";

export type HeightCache = Map<string, number>;

export interface VirtualDisplayUnit {
  key: string;
  unit: DisplayUnit;
  index: number;
  top: number;
  height: number;
  estimatedHeight: number;
  measuredHeight?: number;
}

interface UseThreadVirtualizerOptions {
  units: DisplayUnit[];
  scrollElement: HTMLElement | null;
  viewportHeight: number;
  overscanPx?: number;
  estimateUnitHeight: (unit: DisplayUnit, index: number, units: DisplayUnit[]) => number;
  includeLast?: boolean;
}

export function buildVirtualDisplayUnits(
  units: DisplayUnit[],
  heightCache: HeightCache,
  estimateUnitHeight: (unit: DisplayUnit, index: number, units: DisplayUnit[]) => number,
): VirtualDisplayUnit[] {
  let top = 0;
  return units.map((unit, index) => {
    const key = unitKey(unit, index);
    const estimatedHeight = Math.max(1, Math.ceil(estimateUnitHeight(unit, index, units)));
    const measuredHeight = heightCache.get(key);
    const height = Math.max(1, Math.ceil(measuredHeight ?? estimatedHeight));
    const item: VirtualDisplayUnit = {
      key,
      unit,
      index,
      top,
      height,
      estimatedHeight,
      measuredHeight,
    };
    top += height;
    return item;
  });
}

export function pickVisibleUnits(
  virtualUnits: VirtualDisplayUnit[],
  scrollTop: number,
  viewportHeight: number,
  overscanPx: number,
  includeLast = false,
): VirtualDisplayUnit[] {
  if (virtualUnits.length === 0) return [];
  const start = Math.max(0, scrollTop - overscanPx);
  const end = scrollTop + Math.max(0, viewportHeight) + overscanPx;
  const visible = virtualUnits.filter((item) => {
    const itemEnd = item.top + item.height;
    return itemEnd >= start && item.top <= end;
  });
  const last = virtualUnits[virtualUnits.length - 1];
  if (includeLast && last && !visible.some((item) => item.key === last.key)) {
    visible.push(last);
    visible.sort((a, b) => a.index - b.index);
  }
  return visible;
}

export function useThreadVirtualizer({
  units,
  scrollElement,
  viewportHeight,
  overscanPx = 900,
  estimateUnitHeight,
  includeLast = false,
}: UseThreadVirtualizerOptions) {
  const heightCacheRef = useRef<HeightCache>(new Map());
  const [heightVersion, setHeightVersion] = useState(0);
  const [scrollTop, setScrollTop] = useState(() => scrollElement?.scrollTop ?? 0);

  useEffect(() => {
    if (!scrollElement) {
      setScrollTop(0);
      return;
    }
    const updateScrollTop = () => setScrollTop(scrollElement.scrollTop);
    updateScrollTop();
    scrollElement.addEventListener("scroll", updateScrollTop, { passive: true });
    return () => scrollElement.removeEventListener("scroll", updateScrollTop);
  }, [scrollElement]);

  useEffect(() => {
    const liveKeys = new Set(units.map((unit, index) => unitKey(unit, index)));
    let pruned = false;
    for (const key of heightCacheRef.current.keys()) {
      if (!liveKeys.has(key)) {
        heightCacheRef.current.delete(key);
        pruned = true;
      }
    }
    if (pruned) setHeightVersion((v) => v + 1);
  }, [units]);

  const virtualUnits = useMemo(
    () => buildVirtualDisplayUnits(units, heightCacheRef.current, estimateUnitHeight),
    [estimateUnitHeight, heightVersion, units],
  );
  const totalHeight = virtualUnits.length > 0
    ? virtualUnits[virtualUnits.length - 1].top + virtualUnits[virtualUnits.length - 1].height
    : 0;

  const visibleUnits = useMemo(
    () => pickVisibleUnits(virtualUnits, scrollTop, viewportHeight, overscanPx, includeLast),
    [includeLast, overscanPx, scrollTop, viewportHeight, virtualUnits],
  );

  const measureUnit = useCallback((key: string, height: number) => {
    if (!Number.isFinite(height) || height <= 0) return;
    const rounded = Math.ceil(height);
    const previous = heightCacheRef.current.get(key);
    if (previous != null && Math.abs(previous - rounded) <= 1) return;
    heightCacheRef.current.set(key, rounded);
    setHeightVersion((v) => v + 1);
  }, []);

  return {
    visibleUnits,
    virtualUnits,
    totalHeight,
    measureUnit,
    scrollTop,
  };
}

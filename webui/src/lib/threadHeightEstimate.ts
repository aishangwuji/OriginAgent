import { layout, prepare } from "@chenglou/pretext";

import { marginAfterPrevUnitPx, type DisplayUnit } from "@/components/thread/ThreadMessages";

const ASSISTANT_TEXT_WIDTH = 792;
const USER_TEXT_WIDTH = 576;
const ESTIMATE_CACHE_LIMIT = 2_000;
const estimateCache = new Map<string, number>();
const MARKDOWN_COMPLEX_RE =
  /(^|\n)\s{0,3}(#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|```|~~~|\|.*\|)|(\$\$?|\\\[|\\\(|\*\*|__|`[^`\n]+`|\[[^\]\n]+\([^)]+\))/;

function rememberEstimate(key: string, value: number): number {
  if (estimateCache.size >= ESTIMATE_CACHE_LIMIT) {
    const oldest = estimateCache.keys().next().value as string | undefined;
    if (oldest) estimateCache.delete(oldest);
  }
  estimateCache.set(key, value);
  return value;
}

function estimatePlainTextHeight(
  text: string,
  width: number,
  font: string,
  lineHeight: number,
): number {
  const safeText = text.length > 0 ? text : " ";
  const key = `${width}|${font}|${lineHeight}|${safeText}`;
  const cached = estimateCache.get(key);
  if (cached != null) return cached;

  try {
    if (typeof Intl !== "undefined" && typeof Intl.Segmenter === "function") {
      const prepared = prepare(safeText, font, {
        whiteSpace: "pre-wrap",
        letterSpacing: 0,
      });
      const measured = layout(prepared, width, lineHeight);
      return rememberEstimate(key, Math.ceil(measured.height));
    }
  } catch {
    // Fall through to the rough character-count estimate below.
  }

  const avgCharWidth = font.includes("16px") ? 8 : 7.5;
  const lines = safeText
    .split("\n")
    .reduce((sum, line) => sum + Math.max(1, Math.ceil(line.length * avgCharWidth / width)), 0);
  return rememberEstimate(key, Math.ceil(lines * lineHeight));
}

function isComplexMarkdown(text: string): boolean {
  return MARKDOWN_COMPLEX_RE.test(text);
}

function messageText(unit: DisplayUnit): string {
  if (unit.type === "cluster") {
    return unit.messages.map((m) => `${m.content}\n${m.reasoning ?? ""}`).join("\n");
  }
  return `${unit.message.content}\n${unit.message.reasoning ?? ""}`;
}

export function estimateThreadUnitHeight(
  unit: DisplayUnit,
  index: number,
  units: DisplayUnit[],
): number {
  const margin = index > 0 ? marginAfterPrevUnitPx(units[index - 1]) : 0;

  if (unit.type === "cluster") {
    return margin + 34;
  }

  const m = unit.message;
  const hasMedia = (m.images?.length ?? 0) > 0 || (m.media?.length ?? 0) > 0;
  const text = m.content.trim();
  const reasoningText = m.reasoning?.trim() ?? "";
  const hasReasoning = reasoningText.length > 0 || !!m.reasoningStreaming;

  if (m.role === "user") {
    if (hasMedia) return margin + (text.length > 0 ? 260 : 210);
    const textHeight = estimatePlainTextHeight(
      text,
      USER_TEXT_WIDTH,
      '400 16px / 28px system-ui, sans-serif',
      28,
    );
    return margin + textHeight + 18;
  }

  if (m.role === "assistant") {
    if (text.length === 0) {
      return margin + (hasReasoning ? 38 : 28);
    }
    const combined = messageText(unit);
    if (hasMedia || isComplexMarkdown(combined)) {
      const roughLines = Math.max(2, Math.ceil(combined.length / 84));
      return margin + Math.min(520, 38 + roughLines * 24 + (hasReasoning ? 42 : 0));
    }
    const textHeight = estimatePlainTextHeight(
      text,
      ASSISTANT_TEXT_WIDTH,
      '400 15px / 24px system-ui, sans-serif',
      24,
    );
    return margin + textHeight + (m.isStreaming ? 8 : 42) + (hasReasoning ? 42 : 0);
  }

  if (m.kind === "trace") {
    return margin + 34;
  }

  return margin + 48;
}

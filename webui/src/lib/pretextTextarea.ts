import { layout, prepare } from "@chenglou/pretext";

const MIN_WIDTH_FOR_LAYOUT = 12;

function parsePx(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function fontShorthand(style: CSSStyleDeclaration): string {
  if (style.font && style.font !== "") return style.font;
  const lineHeight = style.lineHeight === "normal" ? "normal" : style.lineHeight;
  return [
    style.fontStyle || "normal",
    style.fontVariant || "normal",
    style.fontWeight || "400",
    `${style.fontSize || "16px"} / ${lineHeight}`,
    style.fontFamily || "sans-serif",
  ].join(" ");
}

function lineHeightPx(style: CSSStyleDeclaration): number {
  const fontSize = parsePx(style.fontSize) || 16;
  if (!style.lineHeight || style.lineHeight === "normal") return fontSize * 1.35;
  const direct = parsePx(style.lineHeight);
  if (direct > 0) return direct;
  const multiplier = Number.parseFloat(style.lineHeight);
  return Number.isFinite(multiplier) ? fontSize * multiplier : fontSize * 1.35;
}

function fallbackScrollHeight(el: HTMLTextAreaElement, maxHeight: number): number {
  const previous = el.style.height;
  el.style.height = "auto";
  const next = Math.min(el.scrollHeight, maxHeight);
  el.style.height = previous;
  return next;
}

export function measureTextareaHeightWithPretext(
  el: HTMLTextAreaElement,
  maxHeight = 260,
): number {
  try {
    if (typeof window === "undefined" || typeof window.getComputedStyle !== "function") {
      return fallbackScrollHeight(el, maxHeight);
    }
    if (typeof Intl === "undefined" || typeof Intl.Segmenter !== "function") {
      return fallbackScrollHeight(el, maxHeight);
    }

    const style = window.getComputedStyle(el);
    const borderBoxWidth = el.clientWidth;
    const paddingX = parsePx(style.paddingLeft) + parsePx(style.paddingRight);
    const paddingY = parsePx(style.paddingTop) + parsePx(style.paddingBottom);
    const contentWidth = borderBoxWidth - paddingX;
    if (contentWidth < MIN_WIDTH_FOR_LAYOUT) return fallbackScrollHeight(el, maxHeight);

    const text = el.value.length > 0 ? el.value : el.placeholder || " ";
    const prepared = prepare(text, fontShorthand(style), {
      whiteSpace: "pre-wrap",
      letterSpacing: parsePx(style.letterSpacing),
    });
    const measured = layout(prepared, contentWidth, lineHeightPx(style));
    const next = Math.ceil(measured.height + paddingY);
    return Math.min(Math.max(next, 0), maxHeight);
  } catch {
    return fallbackScrollHeight(el, maxHeight);
  }
}

export function applyMeasuredTextareaHeight(
  el: HTMLTextAreaElement,
  maxHeight = 260,
): void {
  const height = measureTextareaHeightWithPretext(el, maxHeight);
  el.style.height = `${height}px`;
}

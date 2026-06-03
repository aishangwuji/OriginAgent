import { Suspense, lazy } from "react";

import { cn } from "@/lib/utils";

interface MarkdownTextProps {
  children: string;
  className?: string;
}

const loadMarkdownRenderer = () => import("@/components/MarkdownTextRenderer");
const LazyMarkdownRenderer = lazy(loadMarkdownRenderer);
const MARKDOWN_HINT_RE =
  /(^|\n)\s{0,3}(#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|```|~~~)|(\*\*|__|`[^`\n]+`|\[[^\]\n]+\]\([^)]+\))/;

export function preloadMarkdownText(): void {
  void loadMarkdownRenderer();
}

function looksLikeMarkdown(value: string): boolean {
  return MARKDOWN_HINT_RE.test(value);
}

function MarkdownFallback({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  if (looksLikeMarkdown(children)) {
    return (
      <div
        aria-label="Rendering Markdown"
        className={cn("space-y-2 py-1", className)}
      >
        <div className="h-3.5 w-2/3 animate-pulse rounded-full bg-muted/65" />
        <div className="h-3.5 w-11/12 animate-pulse rounded-full bg-muted/45" />
        <div className="h-3.5 w-4/5 animate-pulse rounded-full bg-muted/45" />
      </div>
    );
  }
  return (
    <div
      className={cn(
        "whitespace-pre-wrap break-words leading-relaxed text-foreground/92",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * Lightweight markdown renderer mirroring agent-chat-ui: GFM + math via
 * ``remark-math`` / ``rehype-katex``, and fenced code blocks delegated to
 * ``CodeBlock`` for copy-to-clipboard and syntax highlighting.
 */
export function MarkdownText({ children, className }: MarkdownTextProps) {
  return (
    <Suspense
      fallback={<MarkdownFallback className={className}>{children}</MarkdownFallback>}
    >
      <LazyMarkdownRenderer className={className}>{children}</LazyMarkdownRenderer>
    </Suspense>
  );
}

import { type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  buildDisplayUnits,
  VirtualThreadMessages,
} from "@/components/thread/ThreadMessages";
import { Button } from "@/components/ui/button";
import { useThreadVirtualizer } from "@/hooks/useThreadVirtualizer";
import { estimateThreadUnitHeight } from "@/lib/threadHeightEstimate";
import { cn } from "@/lib/utils";
import type { UIMessage } from "@/lib/types";

interface ThreadViewportProps {
  messages: UIMessage[];
  isStreaming: boolean;
  composer: ReactNode;
  emptyState?: ReactNode;
  pendingThreadLayout?: boolean;
  scrollToBottomSignal?: number;
  conversationKey?: string | null;
}

const NEAR_BOTTOM_PX = 48;

export function ThreadViewport({
  messages,
  isStreaming,
  composer,
  emptyState,
  pendingThreadLayout = false,
  scrollToBottomSignal = 0,
  conversationKey = null,
}: ThreadViewportProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [scrollElement, setScrollElement] = useState<HTMLDivElement | null>(null);
  const [viewportHeight, setViewportHeight] = useState(0);
  const contentRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const lastConversationKeyRef = useRef<string | null>(conversationKey);
  const pendingConversationScrollRef = useRef(true);
  const scrollFrameIdsRef = useRef<number[]>([]);
  const conversationScrollSettleTimerRef = useRef<number | null>(null);
  /** User scrolled away from the bottom; do not auto-yank until they return or we reset (new chat / send). */
  const userReadingHistoryRef = useRef(false);
  const [atBottom, setAtBottom] = useState(true);
  const hasMessages = messages.length > 0;
  const useThreadLayout = hasMessages || pendingThreadLayout;
  const units = useMemo(() => buildDisplayUnits(messages), [messages]);
  const {
    visibleUnits,
    totalHeight,
    measureUnit,
  } = useThreadVirtualizer({
    units,
    scrollElement,
    viewportHeight,
    overscanPx: 900,
    estimateUnitHeight: estimateThreadUnitHeight,
    includeLast: isStreaming,
  });

  const setScrollNode = useCallback((node: HTMLDivElement | null) => {
    scrollRef.current = node;
    setScrollElement(node);
  }, []);

  const cancelScheduledBottomScroll = useCallback(() => {
    for (const id of scrollFrameIdsRef.current) {
      window.cancelAnimationFrame(id);
    }
    scrollFrameIdsRef.current = [];
  }, []);

  const clearConversationSettleTimer = useCallback(() => {
    if (conversationScrollSettleTimerRef.current === null) return;
    window.clearTimeout(conversationScrollSettleTimerRef.current);
    conversationScrollSettleTimerRef.current = null;
  }, []);

  const scrollToBottomNow = useCallback((smooth = false) => {
    const el = scrollRef.current;
    const marker = bottomRef.current;
    const behavior: ScrollBehavior = smooth ? "smooth" : "auto";
    if (marker) {
      marker.scrollIntoView({ block: "end", behavior });
    } else if (el) {
      el.scrollTo({ top: el.scrollHeight, behavior });
    }
    setAtBottom(true);
  }, []);

  const scrollToBottom = useCallback(
    (smooth = false, frames = 1, options?: { force?: boolean }) => {
      const force = options?.force ?? false;
      cancelScheduledBottomScroll();
      const run = () => {
        if (!force && userReadingHistoryRef.current) return;
        scrollToBottomNow(smooth);
      };
      run();
      for (let i = 1; i < frames; i += 1) {
        const id = window.requestAnimationFrame(() => {
          if (!force && userReadingHistoryRef.current) return;
          scrollToBottomNow(smooth);
        });
        scrollFrameIdsRef.current.push(id);
      }
    },
    [cancelScheduledBottomScroll, scrollToBottomNow],
  );

  useEffect(() => {
    if (!atBottom) return;
    // Instant jump: CSS scroll-smooth + behavior "auto" still animates in some
    // browsers; session switches and history hydration should never slide from top.
    scrollToBottom(false);
  }, [messages, atBottom, scrollToBottom]);

  useEffect(() => {
    if (scrollToBottomSignal <= 0) return;
    userReadingHistoryRef.current = false;
    scrollToBottom(false, 8);
  }, [scrollToBottomSignal, scrollToBottom]);

  useLayoutEffect(() => {
    if (lastConversationKeyRef.current === conversationKey) return;
    lastConversationKeyRef.current = conversationKey;
    clearConversationSettleTimer();
    pendingConversationScrollRef.current = true;
    userReadingHistoryRef.current = false;
    setAtBottom(true);
  }, [clearConversationSettleTimer, conversationKey]);

  useLayoutEffect(() => {
    if (!pendingConversationScrollRef.current) return;
    if (!conversationKey) {
      clearConversationSettleTimer();
      pendingConversationScrollRef.current = false;
      scrollToBottom(false, 4);
      return;
    }
    scrollToBottom(false, 12, { force: true });
    if (!hasMessages) return;
    clearConversationSettleTimer();
    conversationScrollSettleTimerRef.current = window.setTimeout(() => {
      pendingConversationScrollRef.current = false;
      conversationScrollSettleTimerRef.current = null;
    }, 1_200);
  }, [clearConversationSettleTimer, conversationKey, hasMessages, messages, scrollToBottom]);

  useEffect(
    () => () => {
      cancelScheduledBottomScroll();
      clearConversationSettleTimer();
    },
    [cancelScheduledBottomScroll, clearConversationSettleTimer],
  );

  useEffect(() => {
    const target = contentRef.current;
    if (!target || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const settlingConversation = pendingConversationScrollRef.current;
      if (!settlingConversation && userReadingHistoryRef.current) return;
      scrollToBottom(false, settlingConversation ? 8 : 4, {
        force: settlingConversation,
      });
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, [hasMessages, scrollToBottom]);

  useEffect(() => {
    const el = scrollElement;
    if (!el) return;

    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      const near = distance < NEAR_BOTTOM_PX;
      setAtBottom(near);
      userReadingHistoryRef.current = !near;
    };

    onScroll();
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [scrollElement]);

  useLayoutEffect(() => {
    const el = scrollElement;
    if (!el) return;
    const updateViewportHeight = () => setViewportHeight(el.clientHeight);
    updateViewportHeight();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateViewportHeight);
    observer.observe(el);
    return () => observer.disconnect();
  }, [scrollElement]);

  return (
    <div className="relative flex min-h-0 flex-1 overflow-hidden">
      <div
        ref={setScrollNode}
        className={cn(
          "absolute inset-0 overflow-y-auto scroll-auto scrollbar-thin",
          "[&::-webkit-scrollbar]:w-1.5",
          "[&::-webkit-scrollbar-thumb]:rounded-full",
          "[&::-webkit-scrollbar-thumb]:bg-muted-foreground/30",
          "[&::-webkit-scrollbar-track]:bg-transparent",
        )}
      >
        {useThreadLayout ? (
          <div ref={contentRef} className="mx-auto flex min-h-full w-full max-w-[64rem] flex-col">
            <div className="flex-1 px-4 pb-20 pt-4">
              <div className="mx-auto w-full max-w-[49.5rem]">
                {hasMessages ? (
                  <VirtualThreadMessages
                    virtualItems={visibleUnits}
                    totalHeight={totalHeight}
                    allUnits={units}
                    isStreaming={isStreaming}
                    onUnitMeasured={measureUnit}
                  />
                ) : (
                  emptyState
                )}
              </div>
            </div>

            <div className="sticky bottom-0 z-10 mt-auto bg-transparent">
              <div
                aria-hidden
                className={cn(
                  "pointer-events-none absolute inset-x-0 bottom-0 h-36",
                  "bg-gradient-to-t from-card/80 via-card/45 to-transparent",
                  "dark:from-card/65 dark:via-card/30",
                )}
              />
              <div className="relative px-4 pb-3">
                {composer}
              </div>
            </div>
          </div>
        ) : (
          <div ref={contentRef} className="mx-auto flex min-h-full w-full max-w-[72rem] flex-col px-4">
            <div className="flex w-full flex-1 items-center justify-center pb-[3vh] pt-[11vh]">
              <div className="flex w-full max-w-[58rem] flex-col gap-6">
                {emptyState}
                <div className="w-full">{composer}</div>
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} aria-hidden className="h-px" />
      </div>

      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-background to-transparent"
      />

      {!atBottom && (
        <Button
          variant="outline"
          size="icon"
          onClick={() => scrollToBottom(true, 1, { force: true })}
          className={cn(
            /* Keep clear of sticky composer (textarea + toolbar + optional goal strip). */
            "absolute bottom-[8.75rem] left-1/2 z-20 h-8 w-8 -translate-x-1/2 rounded-full shadow-md",
            "bg-background/90 backdrop-blur",
            "animate-in fade-in-0 zoom-in-95",
          )}
          aria-label={t("thread.scrollToBottom")}
        >
          <ArrowDown className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}

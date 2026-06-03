import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThreadViewport } from "@/components/thread/ThreadViewport";
import type { UIMessage } from "@/lib/types";

const messages: UIMessage[] = [
  {
    id: "u1",
    role: "user",
    content: "hello",
    createdAt: Date.now(),
  },
];

const emptyMessages: UIMessage[] = [];

describe("ThreadViewport", () => {
  it("resets to the bottom when opening a different conversation", async () => {
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;

    try {
      const { container, rerender } = render(
        <ThreadViewport
          messages={messages}
          isStreaming={false}
          composer={<div />}
          conversationKey="chat-a"
        />,
      );
      const scroller = container.firstElementChild?.firstElementChild as HTMLElement;
      Object.defineProperties(scroller, {
        scrollHeight: { configurable: true, value: 2400 },
        clientHeight: { configurable: true, value: 600 },
        scrollTop: { configurable: true, value: 0 },
      });
      act(() => {
        scroller.dispatchEvent(new Event("scroll"));
      });
      scrollIntoView.mockClear();

      rerender(
        <ThreadViewport
          messages={messages}
          isStreaming={false}
          composer={<div />}
          conversationKey="chat-b"
        />,
      );

      await waitFor(() =>
        expect(scrollIntoView).toHaveBeenCalledWith({
          block: "end",
          behavior: "auto",
        }),
      );
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("waits for hydrated messages before fulfilling open-chat bottom scroll", async () => {
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;

    try {
      const { container, rerender } = render(
        <ThreadViewport
          messages={emptyMessages}
          isStreaming={false}
          composer={<div />}
          conversationKey={null}
        />,
      );
      const scroller = container.firstElementChild?.firstElementChild as HTMLElement;
      Object.defineProperty(scroller, "scrollHeight", {
        configurable: true,
        value: 0,
      });
      scrollIntoView.mockClear();

      rerender(
        <ThreadViewport
          messages={emptyMessages}
          isStreaming={false}
          composer={<div />}
          conversationKey="chat-a"
        />,
      );
      expect(scrollIntoView).toHaveBeenCalledWith({
        block: "end",
        behavior: "auto",
      });

      Object.defineProperty(scroller, "scrollHeight", {
        configurable: true,
        value: 2400,
      });
      scrollIntoView.mockClear();

      rerender(
        <ThreadViewport
          messages={messages}
          isStreaming={false}
          composer={<div />}
          conversationKey="chat-a"
        />,
      );

      await waitFor(() =>
        expect(scrollIntoView).toHaveBeenCalledWith({
          block: "end",
          behavior: "auto",
        }),
      );
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("keeps forcing bottom scroll while a newly opened conversation settles", async () => {
    vi.useFakeTimers();
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    const OriginalResizeObserver = globalThis.ResizeObserver;
    const resizeCallbacks: ResizeObserverCallback[] = [];
    class MockResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        resizeCallbacks.push(callback);
      }
      observe = vi.fn();
      disconnect = vi.fn();
      unobserve = vi.fn();
    }
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    vi.stubGlobal("ResizeObserver", MockResizeObserver);

    try {
      const { container, rerender } = render(
        <ThreadViewport
          messages={emptyMessages}
          isStreaming={false}
          composer={<div />}
          conversationKey={null}
        />,
      );
      const scroller = container.firstElementChild?.firstElementChild as HTMLElement;
      Object.defineProperties(scroller, {
        scrollHeight: { configurable: true, value: 2400 },
        clientHeight: { configurable: true, value: 600 },
        scrollTop: { configurable: true, value: 900 },
      });

      rerender(
        <ThreadViewport
          messages={messages}
          isStreaming={false}
          composer={<div />}
          conversationKey="chat-a"
        />,
      );
      scrollIntoView.mockClear();
      act(() => {
        scroller.dispatchEvent(new Event("scroll"));
      });
      expect(scrollIntoView).not.toHaveBeenCalled();

      act(() => {
        for (const callback of resizeCallbacks) callback([], {} as ResizeObserver);
      });
      expect(scrollIntoView).toHaveBeenCalledWith({
        block: "end",
        behavior: "auto",
      });

      scrollIntoView.mockClear();
      act(() => {
        vi.advanceTimersByTime(1_250);
        for (const callback of resizeCallbacks) callback([], {} as ResizeObserver);
      });
      expect(scrollIntoView).not.toHaveBeenCalled();
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
      vi.useRealTimers();
      if (OriginalResizeObserver) {
        vi.stubGlobal("ResizeObserver", OriginalResizeObserver);
      } else {
        vi.unstubAllGlobals();
      }
    }
  });

  it("scrolls to the bottom when explicitly signalled after send", async () => {
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;

    try {
      const { container, rerender } = render(
        <ThreadViewport
          messages={messages}
          isStreaming={false}
          composer={<div />}
          scrollToBottomSignal={0}
        />,
      );
      const scroller = container.firstElementChild?.firstElementChild as HTMLElement;
      Object.defineProperty(scroller, "scrollHeight", {
        configurable: true,
        value: 2400,
      });
      scrollIntoView.mockClear();

      rerender(
        <ThreadViewport
          messages={messages}
          isStreaming={false}
          composer={<div />}
          scrollToBottomSignal={1}
        />,
      );

      await waitFor(() =>
        expect(scrollIntoView).toHaveBeenCalledWith({
          block: "end",
          behavior: "auto",
        }),
      );
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it("positions the scroll-to-bottom button close to the composer", () => {
    const { container } = render(
      <ThreadViewport
        messages={messages}
        isStreaming={false}
        composer={<div />}
        conversationKey="chat-a"
      />,
    );
    const scroller = container.firstElementChild?.firstElementChild as HTMLElement;
    Object.defineProperties(scroller, {
      scrollHeight: { configurable: true, value: 2400 },
      clientHeight: { configurable: true, value: 600 },
      scrollTop: { configurable: true, value: 900 },
    });

    act(() => {
      scroller.dispatchEvent(new Event("scroll"));
    });

    expect(screen.getByRole("button", { name: "Scroll to bottom" })).toHaveClass(
      "bottom-[8.75rem]",
    );
  });

  it("virtualizes long threads to the visible window plus overscan", () => {
    const longMessages: UIMessage[] = Array.from({ length: 120 }, (_, i) => ({
      id: `m${i}`,
      role: i % 2 === 0 ? "user" : "assistant",
      content: `message ${i}`,
      createdAt: i,
    }));
    const { container } = render(
      <ThreadViewport
        messages={longMessages}
        isStreaming={false}
        composer={<div />}
        conversationKey="chat-long"
      />,
    );
    const renderedMessages = container.querySelectorAll(".animate-in");

    expect(renderedMessages.length).toBeGreaterThan(0);
    expect(renderedMessages.length).toBeLessThan(longMessages.length);
  });
});

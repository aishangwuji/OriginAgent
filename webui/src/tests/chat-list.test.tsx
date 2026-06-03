import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChatList } from "@/components/ChatList";
import i18n from "@/i18n";
import type { ChatSummary } from "@/lib/types";

const pendingSession: ChatSummary = {
  key: "websocket:1234567890",
  channel: "websocket",
  chatId: "1234567890",
  createdAt: "2026-05-18T09:00:00Z",
  updatedAt: "2026-05-18T09:00:00Z",
  title: "",
  preview: "",
};

describe("ChatList", () => {
  it("shows a pending new chat title before the server generates a real title", async () => {
    await i18n.changeLanguage("zh-CN");

    render(
      <ChatList
        sessions={[pendingSession]}
        activeKey={pendingSession.key}
        onSelect={() => {}}
        onRequestDelete={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "新对话" })).toBeInTheDocument();
    expect(screen.queryByText(/对话 123456/)).not.toBeInTheDocument();
  });

  it("keeps real titles and previews ahead of the pending fallback", async () => {
    await i18n.changeLanguage("en");

    render(
      <ChatList
        sessions={[{ ...pendingSession, title: "Kitchen plan" }]}
        activeKey={null}
        onSelect={() => {}}
        onRequestDelete={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "Kitchen plan" })).toBeInTheDocument();
    expect(screen.queryByText("New chat")).not.toBeInTheDocument();
  });
});

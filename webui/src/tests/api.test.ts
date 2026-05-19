import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  deleteMcpServerSettings,
  deleteSession,
  fetchWebuiThread,
  listSessions,
  listSlashCommands,
  updateBackgroundReviewSettings,
  updateProviderSettings,
  updateSettings,
  updateWebSearchSettings,
  upsertHomeAssistantMcpSettings,
  upsertMcpServerSettings,
} from "@/lib/api";

describe("webui API helpers", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ deleted: true, key: "websocket:chat-1", messages: [] }),
      }),
    );
  });

  it("percent-encodes websocket keys when fetching webui-thread snapshot", async () => {
    await fetchWebuiThread("tok", "websocket:chat-1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/websocket%3Achat-1/webui-thread",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
        credentials: "same-origin",
      }),
    );
  });

  it("percent-encodes websocket keys when deleting a session", async () => {
    await deleteSession("tok", "websocket:chat-1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/websocket%3Achat-1/delete",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes settings updates as a narrow query string", async () => {
    await updateSettings("tok", {
      model: "openrouter/test",
      provider: "openrouter",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/update?model=openrouter%2Ftest&provider=openrouter",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes provider settings updates without returning secrets", async () => {
    await updateProviderSettings("tok", {
      provider: "openrouter",
      apiKey: "sk-or-test",
      apiBase: "https://openrouter.ai/api/v1",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/provider/update?provider=openrouter&api_key=sk-or-test&api_base=https%3A%2F%2Fopenrouter.ai%2Fapi%2Fv1",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes web search settings updates", async () => {
    await updateWebSearchSettings("tok", {
      provider: "searxng",
      baseUrl: "https://search.example.com",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/web-search/update?provider=searxng&base_url=https%3A%2F%2Fsearch.example.com",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes background review setting updates", async () => {
    await updateBackgroundReviewSettings("tok", true);

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/learning/background-review/update?enabled=true",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes MCP server upserts as encoded JSON", async () => {
    await upsertMcpServerSettings("tok", {
      name: "github",
      type: "stdio",
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-github"],
      env: { GITHUB_TOKEN: "ghp_test" },
      enabled_tools: ["*"],
      tool_timeout: 30,
    });

    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url.startsWith("/api/settings/mcp/upsert?")).toBe(true);
    const query = new URLSearchParams(url.split("?")[1]);
    expect(JSON.parse(query.get("config") ?? "{}")).toMatchObject({
      name: "github",
      type: "stdio",
      command: "npx",
      env: { GITHUB_TOKEN: "ghp_test" },
    });
  });

  it("serializes Home Assistant MCP quick config requests", async () => {
    await upsertHomeAssistantMcpSettings("tok", {
      name: "home_assistant",
      address: "http://localhost:8123/home/0",
      token: "ha_token",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/mcp/home-assistant/upsert?name=home_assistant&address=http%3A%2F%2Flocalhost%3A8123%2Fhome%2F0&token=ha_token",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("reports HTML responses as API route/version errors", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "text/html" }),
      text: async () => "<!doctype html><html></html>",
    } as Response);

    await expect(
      upsertHomeAssistantMcpSettings("tok", {
        name: "home_assistant",
        address: "http://localhost:8123",
        token: "ha_token",
      }),
    ).rejects.toThrow(ApiError);
  });

  it("serializes MCP server delete requests", async () => {
    await deleteMcpServerSettings("tok", "github");

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/mcp/delete?name=github",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("maps generated session titles from the sessions list", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        sessions: [
          {
            key: "websocket:chat-1",
            created_at: "2026-05-01T10:00:00",
            updated_at: "2026-05-01T10:01:00",
            title: "优化 WebUI 标题",
          },
        ],
      }),
    } as Response);

    await expect(listSessions("tok")).resolves.toMatchObject([
      {
        key: "websocket:chat-1",
        title: "优化 WebUI 标题",
        preview: "",
      },
    ]);
  });

  it("maps slash command metadata from the commands endpoint", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        commands: [
          {
            command: "/stop",
            title: "Stop current task",
            description: "Cancel the active task.",
            icon: "square",
          },
          {
            command: "/restart",
            title: "Restart OpenHome",
            description: "Restart the bot process.",
            icon: "rotate-cw",
          },
          {
            command: "/history",
            title: "Show conversation history",
            description: "Print the last N messages.",
            icon: "history",
            arg_hint: "[n]",
          },
          {
            command: "/mcp",
            title: "Show MCP servers",
            description: "List configured MCP servers and registered capabilities.",
            icon: "server",
          },
          {
            command: "/skill",
            title: "Show skills",
            description: "List available agent skills and where they come from.",
            icon: "graduation-cap",
          },
        ],
      }),
    } as Response);

    await expect(listSlashCommands("tok")).resolves.toEqual([
      {
        command: "/stop",
        title: "Stop current task",
        description: "Cancel the active task.",
        icon: "square",
        argHint: "",
      },
      {
        command: "/restart",
        title: "Restart OpenHome",
        description: "Restart the bot process.",
        icon: "rotate-cw",
        argHint: "",
      },
      {
        command: "/history",
        title: "Show conversation history",
        description: "Print the last N messages.",
        icon: "history",
        argHint: "[n]",
      },
      {
        command: "/mcp",
        title: "Show MCP servers",
        description: "List configured MCP servers and registered capabilities.",
        icon: "server",
        argHint: "",
      },
      {
        command: "/skill",
        title: "Show skills",
        description: "List available agent skills and where they come from.",
        icon: "graduation-cap",
        argHint: "",
      },
    ]);
    expect(fetch).toHaveBeenCalledWith(
      "/api/commands",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("does not invent slash commands missing from the commands endpoint", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        commands: [
          {
            command: "/history",
            title: "Show conversation history",
            description: "Print the last N messages.",
            icon: "history",
            arg_hint: "[n]",
          },
        ],
      }),
    } as Response);

    await expect(listSlashCommands("tok")).resolves.toEqual([
      {
        command: "/history",
        title: "Show conversation history",
        description: "Print the last N messages.",
        icon: "history",
        argHint: "[n]",
      },
    ]);
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  deleteMcpServerSettings,
  deleteSession,
  domainPackAction,
  fetchDomain,
  fetchProviderModels,
  fetchSelfModel,
  fetchWebuiThread,
  fetchReviewProposal,
  fetchSkill,
  installDomainPack,
  listDomains,
  listSessions,
  listReviewProposals,
  listSkills,
  listSlashCommands,
  reviewProposalAction,
  skillLifecycleAction,
  updateBackgroundReviewSettings,
  updateProviderSettings,
  updateRuntimeSettings,
  updateSettings,
  updateVoiceSettings,
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

  it("fetches the self model from the dedicated endpoint", async () => {
    await fetchSelfModel("tok");

    expect(fetch).toHaveBeenCalledWith(
      "/api/self",
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

  it("serializes provider model fetch requests using the Phase 1 contract route", async () => {
    await fetchProviderModels("tok", {
      provider: "openrouter",
      apiKey: "sk-or-test",
      apiBase: "https://openrouter.ai/api/v1",
      forceRefresh: true,
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/settings/provider/models?provider=openrouter&api_key=sk-or-test&api_base=https%3A%2F%2Fopenrouter.ai%2Fapi%2Fv1&force_refresh=true",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("returns the richer provider models payload", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        provider: "openrouter",
        status: "available",
        catalog_kind: "catalog",
        models: [{ id: "gpt-4o-mini", owned_by: "openai" }],
        model_count: 1,
        fetched_at: 1717171717,
        source_url: "https://openrouter.ai/api/v1/models",
        cached: true,
        phase: "fetch",
      }),
      headers: new Headers({ "content-type": "application/json" }),
    } as Response);

    await expect(
      fetchProviderModels("tok", {
        provider: "openrouter",
      }),
    ).resolves.toMatchObject({
      provider: "openrouter",
      status: "available",
      catalog_kind: "catalog",
      model_count: 1,
      cached: true,
      models: [{ id: "gpt-4o-mini", owned_by: "openai" }],
    });
  });

  it("preserves provider capability metadata in settings payloads", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        agent: {
          model: "openrouter/openai/gpt-4o-mini",
          provider: "openrouter",
          resolved_provider: "openrouter",
          has_api_key: true,
        },
        providers: [
          {
            name: "openrouter",
            label: "OpenRouter",
            configured: true,
            default_api_base: "https://openrouter.ai/api/v1",
            model_catalog_kind: "catalog",
          },
        ],
        web_search: { provider: "duckduckgo", providers: [] },
        learning: { background_review: { enabled: true } },
        runtime_controls: {} as Record<string, unknown>,
        mcp: { servers: [] },
        runtime: { config_path: "config.json" },
        requires_restart: false,
      }),
      headers: new Headers({ "content-type": "application/json" }),
    } as Response);

    const { fetchSettings } = await import("@/lib/api");
    await expect(fetchSettings("tok")).resolves.toMatchObject({
      providers: [
        expect.objectContaining({
          name: "openrouter",
          model_catalog_kind: "catalog",
        }),
      ],
    });
  });

  it("surfaces provider model fetch error bodies instead of collapsing to HTTP status", async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () => JSON.stringify({
        message: "All candidates failed: HTTP 404: missing",
        reason: "models_endpoint_missing",
        phase: "fetch",
      }),
    } as Response);

    await expect(
      fetchProviderModels("tok", {
        provider: "openrouter",
        apiBase: "https://openrouter.ai/api/v1",
      }),
    ).rejects.toMatchObject({
      status: 404,
      message: "All candidates failed: HTTP 404: missing",
      reason: "models_endpoint_missing",
      phase: "fetch",
    });
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

  it("serializes runtime settings updates as encoded JSON", async () => {
    await updateRuntimeSettings("tok", {
      channels: { show_reasoning: false },
      execution: { exec_profile: "disabled" },
    });

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/settings/runtime/update?config="),
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes voice settings updates as encoded JSON", async () => {
    await updateVoiceSettings("tok", {
      input_enabled: true,
      transcription_provider: "volcengine",
      transcription_language: "zh",
      max_record_seconds: 9,
    });

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/settings/local-awareness/audio/update?config="),
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    const query = new URLSearchParams(url.split("?")[1]);
    expect(JSON.parse(query.get("config") ?? "{}")).toMatchObject({
      input_enabled: true,
      transcription_provider: "volcengine",
      transcription_language: "zh",
      max_record_seconds: 9,
    });
  });

  it("serializes review proposal list filters", async () => {
    await listReviewProposals("tok", {
      status: "pending",
      type: "memory",
      origin: "curator",
      limit: 50,
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/reviews?status=pending&type=memory&origin=curator&limit=50",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("percent-encodes review proposal ids for detail requests", async () => {
    await fetchReviewProposal("tok", "review:1");

    expect(fetch).toHaveBeenCalledWith(
      "/api/reviews/review%3A1",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes review proposal actions with an optional reason", async () => {
    await reviewProposalAction("tok", "review:1", "apply", "looks good");

    expect(fetch).toHaveBeenCalledWith(
      "/api/reviews/review%3A1/apply?reason=looks+good",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes skill lifecycle list filters", async () => {
    await listSkills("tok", {
      source: "workspace",
      status: "proposed",
      limit: 50,
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/skills?source=workspace&status=proposed&limit=50",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes domain pack list filters", async () => {
    await listDomains("tok", {
      source: "workspace",
      status: "available",
      limit: 50,
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/domains?source=workspace&status=available&limit=50",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("percent-encodes domain pack ids for detail requests", async () => {
    await fetchDomain("tok", "research:alpha");

    expect(fetch).toHaveBeenCalledWith(
      "/api/domains/research%3Aalpha",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes domain pack install requests", async () => {
    await installDomainPack("tok", "D:/packs/research", "seed local pack");

    expect(fetch).toHaveBeenCalledWith(
      "/api/domains/install?source=D%3A%2Fpacks%2Fresearch&reason=seed+local+pack",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes domain pack actions with optional source and reason", async () => {
    await domainPackAction("tok", "research", "upgrade", {
      source: "D:/packs/research-v2",
      reason: "upgrade local copy",
    });

    expect(fetch).toHaveBeenCalledWith(
      "/api/domains/research/upgrade?source=D%3A%2Fpacks%2Fresearch-v2&reason=upgrade+local+copy",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("percent-encodes skill names for detail requests", async () => {
    await fetchSkill("tok", "domain:research/source-synthesis");

    expect(fetch).toHaveBeenCalledWith(
      "/api/skills/domain%3Aresearch%2Fsource-synthesis",
      expect.objectContaining({
        headers: { Authorization: "Bearer tok" },
      }),
    );
  });

  it("serializes skill lifecycle actions", async () => {
    await skillLifecycleAction("tok", "lighting-troubleshooting", "always", {
      enabled: true,
      reason: "trusted",
    });

    expect(fetch).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/skills\/lighting-troubleshooting\/always\?.*enabled=true.*reason=trusted/),
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
            title: "Restart OriginAgent",
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

    await expect(listSlashCommands("tok", "", "zh-CN")).resolves.toEqual([
      {
        command: "/stop",
        title: "Stop current task",
        description: "Cancel the active task.",
        icon: "square",
        argHint: "",
      },
      {
        command: "/restart",
        title: "Restart OriginAgent",
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
      "/api/commands?lang=zh-CN",
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

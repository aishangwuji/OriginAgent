import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatSummary } from "@/lib/types";
import i18n from "@/i18n";
import { fetchBootstrap } from "@/lib/bootstrap";

const connectSpy = vi.fn();
const refreshSpy = vi.fn();
const createChatSpy = vi.fn().mockResolvedValue("chat-1");
const deleteChatSpy = vi.fn();
const toggleThemeSpy = vi.fn();
let mockSessions: ChatSummary[] = [];
let mockSessionsLoading = false;

vi.mock("@/hooks/useSessions", async (importOriginal) => {
  const React = await import("react");
  const actual = await importOriginal<typeof import("@/hooks/useSessions")>();
  return {
    ...actual,
    useSessions: () => {
      const [sessions, setSessions] = React.useState(mockSessions);
      return {
        sessions: mockSessionsLoading ? mockSessions : sessions,
        loading: mockSessionsLoading,
        error: null,
        refresh: refreshSpy,
        createChat: createChatSpy,
        deleteChat: async (key: string) => {
          await deleteChatSpy(key);
          setSessions((prev: ChatSummary[]) => prev.filter((s) => s.key !== key));
        },
      };
    },
  };
});

vi.mock("@/hooks/useTheme", () => ({
  useTheme: () => ({
    theme: "light" as const,
    toggle: toggleThemeSpy,
  }),
}));

vi.mock("@/lib/bootstrap", () => ({
  fetchBootstrap: vi.fn().mockResolvedValue({
    token: "tok",
    ws_path: "/",
    expires_in: 300,
  }),
  deriveWsUrl: vi.fn(() => "ws://test"),
  loadSavedSecret: vi.fn(() => ""),
  saveSecret: vi.fn(),
  clearSavedSecret: vi.fn(),
}));

vi.mock("@/lib/OriginAgent-client", () => {
  class MockClient {
    status = "idle" as const;
    defaultChatId: string | null = null;
    connect = connectSpy;
    onStatus = () => () => {};
    onRuntimeModelUpdate = () => () => {};
    onSessionUpdate = () => () => {};
    onError = () => () => {};
    onChat = () => () => {};
    sendMessage = vi.fn();
    newChat = vi.fn();
    attach = vi.fn();
    close = vi.fn();
    updateUrl = vi.fn();
    getRunStartedAt = vi.fn(() => null);
    getGoalState = vi.fn(() => undefined);
  }

  return { OriginAgentClient: MockClient };
});

import App from "@/App";

describe("App layout", () => {
  beforeEach(() => {
    vi.useRealTimers();
    mockSessions = [];
    mockSessionsLoading = false;
    connectSpy.mockClear();
    refreshSpy.mockReset();
    createChatSpy.mockClear();
    deleteChatSpy.mockReset();
    toggleThemeSpy.mockReset();
    vi.mocked(fetchBootstrap).mockReset();
    vi.mocked(fetchBootstrap).mockResolvedValue({
      token: "tok",
      ws_path: "/",
      expires_in: 300,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
      }),
    );
  });

  it("keeps sidebar layout out of the main thread width contract", async () => {
    const { container } = render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());

    const main = container.querySelector("main");
    expect(main).toBeInTheDocument();
    expect(main).not.toHaveAttribute("style");

    const asideClassNames = Array.from(container.querySelectorAll("aside")).map(
      (el) => el.className,
    );
    expect(asideClassNames.some((cls) => cls.includes("lg:block"))).toBe(true);
  });

  it("switches to the next session when deleting the active chat", async () => {
    mockSessions = [
      {
        key: "websocket:chat-a",
        channel: "websocket",
        chatId: "chat-a",
        createdAt: "2026-04-16T10:00:00Z",
        updatedAt: "2026-04-16T10:00:00Z",
        preview: "First chat",
      },
      {
        key: "websocket:chat-b",
        channel: "websocket",
        chatId: "chat-b",
        createdAt: "2026-04-16T11:00:00Z",
        updatedAt: "2026-04-16T11:00:00Z",
        preview: "Second chat",
      },
    ];

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    await waitFor(() =>
      expect(
        within(sidebar).getByRole("button", { name: /^First chat$/ }),
      ).toBeInTheDocument(),
    );

    fireEvent.pointerDown(screen.getByLabelText("Chat actions for First chat"), {
      button: 0,
    });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));

    await waitFor(() =>
      expect(screen.getByText("Delete this chat?")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(deleteChatSpy).toHaveBeenCalledWith("websocket:chat-a"),
    );
    await waitFor(() =>
      expect(
        within(sidebar).getByRole("button", { name: /^Second chat$/ }),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("Delete this chat?")).not.toBeInTheDocument();
    expect(document.body.style.pointerEvents).not.toBe("none");
  }, 15_000);

  it("opens the settings view from the sidebar footer", async () => {
    mockSessions = [
      {
        key: "websocket:chat-a",
        channel: "websocket",
        chatId: "chat-a",
        createdAt: "2026-04-16T10:00:00Z",
        updatedAt: "2026-04-16T10:00:00Z",
        preview: "Existing chat",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/skills/lighting-troubleshooting/verify")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              result: {
                skill_name: "lighting-troubleshooting",
                status: "proposed",
                action: "verify",
                ok: true,
                message: "Skill verified.",
              },
              skill: {
                name: "lighting-troubleshooting",
                path: "skills/lighting-troubleshooting/SKILL.md",
                source: "workspace",
                description: "Lighting help.",
                body_preview: "Use this skill.",
                lifecycle_status: "proposed",
                verification_status: "verified",
                can_verify: false,
                can_activate: true,
                can_deprecate: true,
                can_reject: true,
                can_toggle_always: false,
              },
              stats: {
                skills_count: 1,
                workspace_skills_count: 1,
                skill_lifecycle_status_counts: { proposed: 1 },
                skill_verification_status_counts: { verified: 1 },
                unverified_skill_count: 0,
                deprecated_skill_count: 0,
                rejected_skill_count: 0,
                always_workspace_skill_count: 0,
              },
            }),
          };
        }
        if (url.includes("/api/skills/lighting-troubleshooting")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              skill: {
                name: "lighting-troubleshooting",
                path: "skills/lighting-troubleshooting/SKILL.md",
                source: "workspace",
                description: "Lighting help.",
                body_preview: "Use this skill.",
                lifecycle_status: "proposed",
                verification_status: "unverified",
                version: "1",
                domain_id: "core",
                effective_always: false,
                can_verify: true,
                can_activate: false,
                can_deprecate: true,
                can_reject: true,
                can_toggle_always: false,
              },
              stats: {
                skills_count: 1,
                workspace_skills_count: 1,
                skill_lifecycle_status_counts: { proposed: 1 },
                skill_verification_status_counts: { unverified: 1 },
                unverified_skill_count: 1,
                deprecated_skill_count: 0,
                rejected_skill_count: 0,
                always_workspace_skill_count: 0,
              },
            }),
          };
        }
        if (url.includes("/api/skills")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              skills: [
                {
                  name: "lighting-troubleshooting",
                  path: "skills/lighting-troubleshooting/SKILL.md",
                  source: "workspace",
                  description: "Lighting help.",
                  lifecycle_status: "proposed",
                  verification_status: "unverified",
                },
              ],
              stats: {
                skills_count: 1,
                workspace_skills_count: 1,
                skill_lifecycle_status_counts: { proposed: 1 },
                skill_verification_status_counts: { unverified: 1 },
                unverified_skill_count: 1,
                deprecated_skill_count: 0,
                rejected_skill_count: 0,
                always_workspace_skill_count: 0,
              },
            }),
          };
        }
        if (url.includes("/api/domains/research")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              domain: {
                id: "research",
                name: "Research",
                version: "0.1.0",
                path: "domain_packs/research",
                source: "workspace",
                status: "available",
                enabled: true,
                active: false,
                verification_status: "verified",
                overrides_builtin: false,
                description: "Research pack.",
                validation_summary: "Domain pack is valid.",
                skills: [{ id: "source-synthesis", status: "available" }],
                workflows: [],
                can_upgrade: true,
                can_uninstall: true,
                can_enable: false,
                can_disable: true,
                can_activate: true,
                can_deactivate: false,
                can_eval: true,
              },
              stats: {
                workspace_domain_pack_count: 1,
                builtin_domain_pack_count: 0,
                domain_pack_status_counts: { available: 1 },
                active_domain_pack_count: 0,
                domain_pack_override_count: 0,
                domain_pack_eval_status_counts: {},
                last_domain_pack_event_at: null,
              },
            }),
          };
        }
        if (url.includes("/api/domains")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              domains: [
                {
                  id: "research",
                  name: "Research",
                  version: "0.1.0",
                  path: "domain_packs/research",
                  source: "workspace",
                  status: "available",
                  enabled: true,
                  active: false,
                  verification_status: "verified",
                  description: "Research pack.",
                },
              ],
              stats: {
                workspace_domain_pack_count: 1,
                builtin_domain_pack_count: 0,
                domain_pack_status_counts: { available: 1 },
                active_domain_pack_count: 0,
                domain_pack_override_count: 0,
                domain_pack_eval_status_counts: {},
                last_domain_pack_event_at: null,
              },
            }),
          };
        }
        if (url.includes("/api/self")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              self_model: {
                schema_version: 1,
                generated_at: "2026-05-20T12:00:00+00:00",
                identity: {
                  agent_name: "OriginAgent",
                  workspace_name: "OriginAgent",
                  runtime_profile: "default",
                  audit_mode: "minimal",
                },
                runtime: {
                  registered_tools_count: 12,
                  active_sessions_count: 1,
                  pending_queue_count: 0,
                  cron_available: false,
                  confirmation_available: true,
                  background_review_enabled: false,
                  curator_enabled: false,
                },
                domains: {
                  stats: {
                    workspace_domain_pack_count: 1,
                    builtin_domain_pack_count: 0,
                    domain_pack_status_counts: { available: 1 },
                    active_domain_pack_count: 1,
                    domain_pack_override_count: 0,
                    domain_pack_eval_status_counts: {},
                    last_domain_pack_event_at: null,
                  },
                  items: [
                    {
                      id: "research",
                      name: "Research",
                      version: "0.1.0",
                      path: "domain_packs/research",
                      source: "workspace",
                      status: "available",
                      enabled: true,
                      active: true,
                      verification_status: "verified",
                      description: "Research pack.",
                    },
                  ],
                },
                skills: {
                  stats: {
                    skills_count: 1,
                    workspace_skills_count: 1,
                    skill_lifecycle_status_counts: { active: 1 },
                    skill_verification_status_counts: { verified: 1 },
                    unverified_skill_count: 0,
                    deprecated_skill_count: 0,
                    rejected_skill_count: 0,
                    always_workspace_skill_count: 0,
                  },
                  items: [
                    {
                      name: "lighting-troubleshooting",
                      path: "skills/lighting-troubleshooting/SKILL.md",
                      source: "workspace",
                      description: "Lighting help.",
                      verification_status: "verified",
                      lifecycle_status: "active",
                    },
                  ],
                },
                workflows: {
                  stats: {
                    workflow_artifacts_count: 1,
                    workflow_artifact_status_counts: { proposed: 1 },
                    invalid_workflow_artifacts_count: 0,
                    workflow_status_counts: { available: 1 },
                    workflow_verification_status_counts: { unverified: 1 },
                    managed_by_domain_pack_count: 0,
                  },
                  items: [
                    {
                      name: "lighting-incident-response",
                      path: "workflows/lighting-incident-response/workflow.yaml",
                      status: "available",
                      proposal_status: "proposed",
                      verification_status: "unverified",
                      domain_id: "core",
                      managed_by_domain_pack: false,
                    },
                  ],
                },
                facts: {
                  active_count: 2,
                  pending_confirmation_count: 1,
                  category_counts: { note: 2 },
                  domain_counts: { general: 2 },
                },
                memory: {
                  has_memory_context: true,
                  recent_history_pending_count: 1,
                },
                reviews: {
                  pending_count: 1,
                  status_counts: { pending: 1 },
                  type_counts: { workflow: 1 },
                  origin_counts: { background_review: 1 },
                },
                confirmations: {
                  pending_count: 1,
                  expired_count: 0,
                  kind_counts: { action_confirmation: 1 },
                  risk_counts: { high: 1 },
                },
                limitations: [
                  {
                    code: "review_pending",
                    status: "info",
                    subject_type: "review",
                    subject_id: "review_workflow",
                    summary: "Workflow review is still pending.",
                  },
                ],
              },
            }),
          };
        }
        if (url.includes("/api/settings")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              agent: {
                model: "openai/gpt-4o",
                provider: "auto",
                resolved_provider: "openai",
                has_api_key: true,
              },
              providers: [
                {
                  name: "openai",
                  label: "OpenAI",
                  configured: true,
                  api_key_hint: "open••••-key",
                },
                {
                  name: "openrouter",
                  label: "OpenRouter",
                  configured: false,
                  default_api_base: "https://openrouter.ai/api/v1",
                },
              ],
              web_search: {
                provider: "brave",
                api_key_hint: "BSAo••••ew20",
                base_url: null,
                providers: [
                  { name: "duckduckgo", label: "DuckDuckGo", credential: "none" },
                  { name: "brave", label: "Brave Search", credential: "api_key" },
                  { name: "tavily", label: "Tavily", credential: "api_key" },
                ],
              },
              mcp: {
                servers: [
                  {
                    name: "github",
                    type: "stdio",
                    command: "npx",
                    args: ["-y", "@modelcontextprotocol/server-github"],
                    env: { GITHUB_TOKEN: "••••" },
                    url: "",
                    headers: {},
                    tool_timeout: 30,
                    enabled_tools: ["*"],
                  },
                ],
              },
              runtime: {
                config_path: "/tmp/config.json",
              },
              learning: {
                background_review: { enabled: false },
              },
              runtime_controls: {
                channels: {
                  send_progress: true,
                  send_tool_hints: false,
                  show_reasoning: true,
                },
                agent: {
                  unified_session: false,
                  cold_archive_enabled: true,
                  allow_agent_initiated_messages: false,
                  auxiliary_enabled: true,
                  domain_packs_enabled: true,
                  provider_retry_mode: "standard",
                  dream_annotate_line_ages: true,
                },
                learning: {
                  background_review_enabled: false,
                  curator_enabled: false,
                },
                evolution: {
                  mode: "conservative",
                  allow_manual_override: false,
                  dry_run: true,
                  outcome_archive_enabled: true,
                  dependency_stale_cleanup_enabled: true,
                  auto_verify_workflows: false,
                  skill_candidates_enabled: false,
                  feedback_calibration_enabled: true,
                  sandbox_enabled: true,
                  trial_enabled: true,
                  trial_isolated_workspace: true,
                  trial_read_only_tools_only: true,
                },
                gateway: { heartbeat_enabled: true },
                security: {
                  pairing_enabled: false,
                  pairing_allow_self_approve: false,
                },
                search: {
                  web_enabled: true,
                  web_fetch_use_jina_reader: true,
                  session_search_enabled: true,
                  session_search_backend: "auto",
                  session_search_semantic_enabled: true,
                  session_search_rebuild_on_start: false,
                  content_read_enabled: false,
                  content_read_use_jina_reader: true,
                },
                execution: {
                  exec_enabled: true,
                  exec_profile: "secure",
                  exec_allow_unsafe_exec: false,
                  exec_shell_syntax_policy: "restricted",
                  my_enabled: true,
                  my_allow_set: false,
                  restrict_to_workspace: false,
                },
                media: { image_generation_enabled: false },
                devices: {
                  device_enabled: false,
                  device_lighting_enabled: false,
                  device_mode: "dry_run",
                  device_backend: "none",
                },
                subagent: { mode: "normal" },
                audit: {
                  audit_mode: "minimal",
                  audit_security_on_policy_denial: true,
                },
                runtime: {
                  profile: "default",
                },
              },
              requires_restart: false,
            }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Settings" }));

    expect(await screen.findByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(document.title).toBe("Settings · OriginAgent");
    expect(screen.queryByRole("navigation", { name: "Sidebar navigation" })).not.toBeInTheDocument();
    const settingsNav = screen.getByRole("navigation", { name: "Settings sections" });
    expect(within(settingsNav).getByRole("button", { name: "General" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(settingsNav).getByRole("button", { name: "BYOK" })).toBeInTheDocument();
    expect(within(settingsNav).getByRole("button", { name: "Self" })).toBeInTheDocument();
    expect(within(settingsNav).getByRole("button", { name: "Skills" })).toBeInTheDocument();
    expect(within(settingsNav).getByRole("button", { name: "Domains" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.getByText("AI")).toBeInTheDocument();
    expect(screen.getByDisplayValue("openai/gpt-4o")).toBeInTheDocument();
    fireEvent.click(within(settingsNav).getByRole("button", { name: "Self" }));
    expect(await screen.findByText("Read-only capability awareness built from the current runtime, governance state, and pending review signals.")).toBeInTheDocument();
    expect(screen.getByText("Known Limitations")).toBeInTheDocument();
    expect(screen.getByText("Workflow review is still pending.")).toBeInTheDocument();
    fireEvent.click(within(settingsNav).getByRole("button", { name: "BYOK" }));
    expect(screen.getByRole("tab", { name: "LLM" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Web Search" })).toBeInTheDocument();
    expect(screen.getByText("OpenRouter")).toBeInTheDocument();
    expect(screen.getAllByText("Not configured").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByText("OpenAI"));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByPlaceholderText("Leave blank to keep the current key"), {
      target: { value: "unsaved-openai-key" },
    });
    fireEvent.click(screen.getByText("OpenRouter"));
    fireEvent.click(screen.getByText("OpenAI"));
    expect(screen.getByText("open••••-key")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("unsaved-openai-key")).not.toBeInTheDocument();

    fireEvent.click(within(settingsNav).getByRole("button", { name: "Domains" }));
    expect(await screen.findByText("Govern local domain packs in the current workspace. Workspace packs can be installed, upgraded, evaluated, activated, and removed; builtin packs stay read-only.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install" })).toBeInTheDocument();

    fireEvent.click(within(settingsNav).getByRole("button", { name: "BYOK" }));
    fireEvent.click(screen.getByRole("tab", { name: "Web Search" }));
    expect(screen.getByText("Search provider")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Brave Search/ })).toBeInTheDocument();
    expect(screen.getByText("BSAo••••ew20")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByPlaceholderText("Leave blank to keep the current key"), {
      target: { value: "unsaved-brave-key" },
    });
    fireEvent.pointerDown(screen.getByRole("button", { name: /Brave Search/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Tavily" }));
    fireEvent.pointerDown(screen.getByRole("button", { name: /Tavily/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Brave Search" }));
    expect(screen.getByText("BSAo••••ew20")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("unsaved-brave-key")).not.toBeInTheDocument();

    fireEvent.click(within(settingsNav).getByRole("button", { name: "Skills" }));
    expect((await screen.findAllByText("lighting-troubleshooting")).length).toBeGreaterThan(0);
    expect(screen.getByText("Lifecycle note")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Skill verified.")).toBeInTheDocument();

    fireEvent.click(within(settingsNav).getByRole("button", { name: "MCP" }));
    expect(screen.getByText("Configured servers")).toBeInTheDocument();
    expect(screen.getByText("github")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByDisplayValue("GITHUB_TOKEN=••••")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add MCP" }));
    expect(screen.getByText("New MCP server")).toBeInTheDocument();
  });

  it("opens reviews from the sidebar and applies a memory proposal", async () => {
    await i18n.changeLanguage("en");
    const proposal = {
      id: "review_memory",
      created_at: "2026-05-19T10:00:00+00:00",
      session_key: "websocket:chat-a",
      turn_id: "turn-1",
      proposal_type: "memory",
      domain_id: "core",
      title: "Remember concise answers",
      content: "User prefers concise answers.",
      rationale: "The user asked for concise responses.",
      confidence: 0.9,
      evidence: ["Please be concise."],
      status: "pending",
    };
    const appliedProposal = {
      ...proposal,
      status: "applied",
      applied_fact_id: "fact_123",
    };
    let applied = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) => ({
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        text: async () => JSON.stringify(body),
      });
      if (url === "/api/reviews?status=pending&limit=50") {
        return json({
          proposals: applied ? [] : [proposal],
          stats: { proposal_count: 1, pending_count: applied ? 0 : 1 },
        });
      }
      if (url === "/api/reviews/review_memory") {
        return json({
          proposal: applied ? appliedProposal : proposal,
          stats: { proposal_count: 1, pending_count: applied ? 0 : 1 },
        });
      }
      if (url === "/api/reviews/review_memory/apply") {
        applied = true;
        return json({
          result: {
            proposal_id: "review_memory",
            status: "applied",
            action: "apply",
            ok: true,
            message: "Review proposal applied.",
            fact_id: "fact_123",
          },
          proposal: appliedProposal,
          stats: { proposal_count: 1, pending_count: 0 },
        });
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Reviews" }));

    expect(await screen.findByRole("heading", { name: "Learning Reviews" })).toBeInTheDocument();
    expect(await screen.findByText("Remember concise answers")).toBeInTheDocument();
    expect(screen.getAllByText("User prefers concise answers.").length).toBeGreaterThan(0);
    expect(document.title).toBe("Learning Reviews · OriginAgent");

    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByText("Apply this proposal?")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Apply" }).at(-1)!);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/reviews/review_memory/apply",
        expect.objectContaining({
          headers: { Authorization: "Bearer tok" },
        }),
      ),
    );
    expect(await screen.findByText("Review proposal applied.")).toBeInTheDocument();
  });

  it("applies a skill proposal with proposed workspace skill confirmation", async () => {
    await i18n.changeLanguage("en");
    const proposal = {
      id: "review_skill",
      created_at: "2026-05-19T10:00:00+00:00",
      session_key: "websocket:chat-a",
      turn_id: "turn-1",
      proposal_type: "skill",
      domain_id: "core",
      title: "Lighting troubleshooting",
      content: "Create a reusable lighting troubleshooting skill.",
      rationale: "The workflow came up repeatedly.",
      confidence: 0.88,
      evidence: ["Check device state first."],
      payload: { skill_name: "lighting-troubleshooting" },
      status: "pending",
      can_apply: true,
    };
    const appliedProposal = {
      ...proposal,
      status: "applied",
      applied_skill_name: "lighting-troubleshooting",
      applied_skill_path: "skills/lighting-troubleshooting/SKILL.md",
      apply_artifact: {
        skill_name: "lighting-troubleshooting",
        path: "skills/lighting-troubleshooting/SKILL.md",
        validation: "Skill artifact is valid.",
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) => ({
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        text: async () => JSON.stringify(body),
      });
      if (url === "/api/reviews?status=pending&limit=50") {
        return json({
          proposals: [proposal],
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      if (url === "/api/reviews/review_skill") {
        return json({
          proposal,
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      if (url === "/api/reviews/review_skill/apply") {
        return json({
          result: {
            proposal_id: "review_skill",
            status: "applied",
            action: "apply",
            ok: true,
            message: "Skill review proposal applied.",
            artifact: appliedProposal.apply_artifact,
          },
          proposal: appliedProposal,
          stats: { proposal_count: 1, pending_count: 0 },
        });
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Reviews" }));

    expect(await screen.findByText("Lighting troubleshooting")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByText(/proposed, unverified workspace skill/)).toBeInTheDocument();
    expect(screen.getByText(/lighting-troubleshooting/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Apply" }).at(-1)!);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/reviews/review_skill/apply",
        expect.objectContaining({
          headers: { Authorization: "Bearer tok" },
        }),
      ),
    );
    expect(await screen.findByText("Skill review proposal applied.")).toBeInTheDocument();
  });

  it("applies a workflow proposal with manual workflow confirmation", async () => {
    await i18n.changeLanguage("en");
    const proposal = {
      id: "review_workflow",
      created_at: "2026-05-19T10:00:00+00:00",
      session_key: "websocket:chat-a",
      turn_id: "turn-1",
      proposal_type: "workflow",
      domain_id: "core",
      title: "Lighting incident response",
      content: "Create a manual lighting incident response workflow.",
      rationale: "The workflow came up repeatedly.",
      confidence: 0.82,
      evidence: ["Check device state first."],
      payload: { workflow_name: "lighting-incident-response" },
      status: "pending",
      can_apply: true,
    };
    const appliedProposal = {
      ...proposal,
      status: "applied",
      applied_workflow_name: "lighting-incident-response",
      applied_workflow_path: "workflows/lighting-incident-response/workflow.yaml",
      apply_artifact: {
        artifact_type: "workflow",
        workflow_name: "lighting-incident-response",
        path: "workflows/lighting-incident-response/workflow.yaml",
        validation: "Workflow artifact is valid.",
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) => ({
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        text: async () => JSON.stringify(body),
      });
      if (url === "/api/reviews?status=pending&limit=50") {
        return json({
          proposals: [proposal],
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      if (url === "/api/reviews/review_workflow") {
        return json({
          proposal,
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      if (url === "/api/reviews/review_workflow/apply") {
        return json({
          result: {
            proposal_id: "review_workflow",
            status: "applied",
            action: "apply",
            ok: true,
            message: "Workflow review proposal applied.",
            artifact: appliedProposal.apply_artifact,
          },
          proposal: appliedProposal,
          stats: { proposal_count: 1, pending_count: 0 },
        });
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Reviews" }));

    expect((await screen.findAllByText("Lighting incident response")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByText(/proposed, unverified manual workflow/)).toBeInTheDocument();
    expect(screen.getByText(/lighting-incident-response/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Apply" }).at(-1)!);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/reviews/review_workflow/apply",
        expect.objectContaining({
          headers: { Authorization: "Bearer tok" },
        }),
      ),
    );
    expect(await screen.findByText("Workflow review proposal applied.")).toBeInTheDocument();
  });

  it("filters curator proposals and applies a promote_skill review", async () => {
    await i18n.changeLanguage("en");
    const proposal = {
      id: "review_promote_skill",
      created_at: "2026-05-20T10:00:00+00:00",
      session_key: "websocket:chat-a",
      turn_id: "turn-7",
      origin: "curator",
      proposal_type: "promote_skill",
      domain_id: "core",
      title: "Promote verified skill `lighting-troubleshooting`",
      content: "Curator recommends activating this verified workspace skill.",
      rationale: "The skill is verified and not blocked by a stronger duplicate.",
      confidence: 0.84,
      evidence: ["skill=lighting-troubleshooting"],
      payload: {
        subject_id: "lighting-troubleshooting",
        subject_type: "skill",
        subject_path: "skills/lighting-troubleshooting/SKILL.md",
        suggested_action: "promote_skill",
      },
      subject_label: "skill:lighting-troubleshooting (skills/lighting-troubleshooting/SKILL.md)",
      suggested_action: "promote_skill",
      status: "pending",
      can_apply: true,
    };
    const appliedProposal = {
      ...proposal,
      status: "applied",
      applied_skill_name: "lighting-troubleshooting",
      applied_skill_path: "skills/lighting-troubleshooting/SKILL.md",
      apply_artifact: {
        artifact_type: "skill",
        skill_name: "lighting-troubleshooting",
        path: "skills/lighting-troubleshooting/SKILL.md",
        validation: "Skill lifecycle action applied.",
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) => ({
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        text: async () => JSON.stringify(body),
      });
      if (url === "/api/reviews?status=pending&limit=50") {
        return json({
          proposals: [],
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      if (url === "/api/reviews?status=pending&origin=curator&limit=50") {
        return json({
          proposals: [proposal],
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      if (url === "/api/reviews/review_promote_skill") {
        return json({
          proposal,
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      if (url === "/api/reviews/review_promote_skill/apply") {
        return json({
          result: {
            proposal_id: "review_promote_skill",
            status: "applied",
            action: "apply",
            ok: true,
            message: "Curator skill promotion applied.",
            artifact: appliedProposal.apply_artifact,
          },
          proposal: appliedProposal,
          stats: { proposal_count: 1, pending_count: 0 },
        });
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Reviews" }));

    fireEvent.click(await screen.findByRole("button", { name: "Curator" }));
    expect(await screen.findByText("Promote verified skill `lighting-troubleshooting`")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByText(/verifying it if needed and activating/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Apply" }).at(-1)!);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/reviews/review_promote_skill/apply",
        expect.objectContaining({
          headers: { Authorization: "Bearer tok" },
        }),
      ),
    );
    expect(await screen.findByText("Curator skill promotion applied.")).toBeInTheDocument();
  });

  it("disables apply for review-only curator proposals", async () => {
    await i18n.changeLanguage("en");
    const proposal = {
      id: "review_merge_skill",
      created_at: "2026-05-20T10:00:00+00:00",
      session_key: "websocket:chat-a",
      turn_id: "turn-8",
      origin: "curator",
      proposal_type: "merge_skill",
      domain_id: "core",
      title: "Review duplicate workspace skills: alpha, beta",
      content: "Curator found duplicate workspace skills that need manual merge review.",
      rationale: "The duplicate group is ambiguous.",
      confidence: 0.78,
      evidence: ["alpha: skills/alpha/SKILL.md", "beta: skills/beta/SKILL.md"],
      payload: {
        subject_id: "alpha,beta",
        subject_type: "skill_group",
        subject_path: "skills/alpha/SKILL.md",
        suggested_action: "merge_skill",
      },
      subject_label: "skill_group:alpha,beta (skills/alpha/SKILL.md)",
      suggested_action: "merge_skill",
      status: "pending",
      can_apply: false,
      unsupported_reason: "merge_skill proposals are review-only in P10.",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) => ({
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        text: async () => JSON.stringify(body),
      });
      if (url === "/api/reviews?status=pending&limit=50") {
        return json({
          proposals: [proposal],
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      if (url === "/api/reviews/review_merge_skill") {
        return json({
          proposal,
          stats: { proposal_count: 1, pending_count: 1 },
        });
      }
      return { ok: false, status: 404, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Reviews" }));

    expect(
      await screen.findByRole("heading", {
        name: "Review duplicate workspace skills: alpha, beta",
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(screen.getByText("merge_skill proposals are review-only in P10.")).toBeInTheDocument();
  });

  it("shows New chat in the thread header while a session has no generated title", async () => {
    await i18n.changeLanguage("zh-CN");
    mockSessions = [
      {
        key: "websocket:1234567890",
        channel: "websocket",
        chatId: "1234567890",
        createdAt: "2026-05-18T10:00:00Z",
        updatedAt: "2026-05-18T10:00:00Z",
        title: "",
        preview: "",
      },
    ];

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "新对话" }));

    await waitFor(() => expect(screen.getAllByText("新对话").length).toBeGreaterThanOrEqual(2));
    expect(screen.queryByText(/对话 123456/)).not.toBeInTheDocument();
    expect(document.title).toBe("新对话 · OriginAgent");
  });

  it("keeps settings in a loading state during transient API failures", async () => {
    let settingsCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("/api/settings")) {
          settingsCalls += 1;
          if (settingsCalls < 3) {
            return {
              ok: false,
              status: 503,
              json: async () => ({}),
            };
          }
          return {
            ok: true,
            status: 200,
            json: async () => ({
              agent: {
                model: "openai/gpt-4o",
                provider: "openai",
                resolved_provider: "openai",
                has_api_key: true,
              },
              providers: [{ name: "openai", label: "OpenAI", configured: true }],
              web_search: {
                provider: "duckduckgo",
                api_key_hint: null,
                base_url: null,
                providers: [
                  { name: "duckduckgo", label: "DuckDuckGo", credential: "none" },
                ],
              },
              mcp: { servers: [] },
              runtime: {
                config_path: "/tmp/config.json",
              },
              learning: {
                background_review: { enabled: false },
              },
              runtime_controls: {
                channels: { send_progress: true, send_tool_hints: false, show_reasoning: true },
                agent: {
                  unified_session: false,
                  cold_archive_enabled: true,
                  allow_agent_initiated_messages: false,
                  auxiliary_enabled: true,
                  domain_packs_enabled: true,
                  provider_retry_mode: "standard",
                  dream_annotate_line_ages: true,
                },
                learning: { background_review_enabled: false, curator_enabled: false },
                evolution: {
                  mode: "conservative",
                  allow_manual_override: false,
                  dry_run: true,
                  outcome_archive_enabled: true,
                  dependency_stale_cleanup_enabled: true,
                  auto_verify_workflows: false,
                  skill_candidates_enabled: false,
                  feedback_calibration_enabled: true,
                  sandbox_enabled: true,
                  trial_enabled: true,
                  trial_isolated_workspace: true,
                  trial_read_only_tools_only: true,
                },
                gateway: { heartbeat_enabled: true },
                security: { pairing_enabled: false, pairing_allow_self_approve: false },
                search: {
                  web_enabled: true,
                  web_fetch_use_jina_reader: true,
                  session_search_enabled: true,
                  session_search_backend: "auto",
                  session_search_semantic_enabled: true,
                  session_search_rebuild_on_start: false,
                  content_read_enabled: false,
                  content_read_use_jina_reader: true,
                },
                execution: {
                  exec_enabled: true,
                  exec_profile: "secure",
                  exec_allow_unsafe_exec: false,
                  exec_shell_syntax_policy: "restricted",
                  my_enabled: true,
                  my_allow_set: false,
                  restrict_to_workspace: false,
                },
                media: { image_generation_enabled: false },
                devices: {
                  device_enabled: false,
                  device_lighting_enabled: false,
                  device_mode: "dry_run",
                  device_backend: "none",
                },
                subagent: { mode: "normal" },
                audit: { audit_mode: "minimal", audit_security_on_policy_denial: true },
                runtime: { profile: "default" },
              },
              requires_restart: false,
            }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Settings" }));
    expect(screen.queryByText("Could not load settings")).not.toBeInTheDocument();

    await waitFor(
      () => expect(screen.getByDisplayValue("openai/gpt-4o")).toBeInTheDocument(),
      { timeout: 3_000 },
    );
    expect(settingsCalls).toBe(3);
    expect(screen.queryByText("Could not load settings")).not.toBeInTheDocument();
  });

  it("refreshes the API token when settings returns 401", async () => {
    let settingsCalls = 0;
    let sawFreshToken = false;
    vi.mocked(fetchBootstrap)
      .mockResolvedValueOnce({
        token: "tok",
        ws_path: "/",
        expires_in: 300,
      })
      .mockResolvedValueOnce({
      token: "fresh-tok",
      ws_path: "/",
      expires_in: 300,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).includes("/api/settings")) {
          settingsCalls += 1;
          if (settingsCalls === 1) {
            return { ok: false, status: 401, json: async () => ({}) };
          }
          if ((init?.headers as Record<string, string>).Authorization === "Bearer fresh-tok") {
            sawFreshToken = true;
          }
          return {
            ok: true,
            status: 200,
            json: async () => ({
              agent: {
                model: "openai/gpt-4o",
                provider: "openai",
                resolved_provider: "openai",
                has_api_key: true,
              },
              providers: [{ name: "openai", label: "OpenAI", configured: true }],
              web_search: {
                provider: "duckduckgo",
                api_key_hint: null,
                base_url: null,
                providers: [
                  { name: "duckduckgo", label: "DuckDuckGo", credential: "none" },
                ],
              },
              mcp: { servers: [] },
              runtime: {
                config_path: "/tmp/config.json",
              },
              learning: {
                background_review: { enabled: false },
              },
              runtime_controls: {
                channels: { send_progress: true, send_tool_hints: false, show_reasoning: true },
                agent: {
                  unified_session: false,
                  cold_archive_enabled: true,
                  allow_agent_initiated_messages: false,
                  auxiliary_enabled: true,
                  domain_packs_enabled: true,
                  provider_retry_mode: "standard",
                  dream_annotate_line_ages: true,
                },
                learning: { background_review_enabled: false, curator_enabled: false },
                evolution: {
                  mode: "conservative",
                  allow_manual_override: false,
                  dry_run: true,
                  outcome_archive_enabled: true,
                  dependency_stale_cleanup_enabled: true,
                  auto_verify_workflows: false,
                  skill_candidates_enabled: false,
                  feedback_calibration_enabled: true,
                  sandbox_enabled: true,
                  trial_enabled: true,
                  trial_isolated_workspace: true,
                  trial_read_only_tools_only: true,
                },
                gateway: { heartbeat_enabled: true },
                security: { pairing_enabled: false, pairing_allow_self_approve: false },
                search: {
                  web_enabled: true,
                  web_fetch_use_jina_reader: true,
                  session_search_enabled: true,
                  session_search_backend: "auto",
                  session_search_semantic_enabled: true,
                  session_search_rebuild_on_start: false,
                  content_read_enabled: false,
                  content_read_use_jina_reader: true,
                },
                execution: {
                  exec_enabled: true,
                  exec_profile: "secure",
                  exec_allow_unsafe_exec: false,
                  exec_shell_syntax_policy: "restricted",
                  my_enabled: true,
                  my_allow_set: false,
                  restrict_to_workspace: false,
                },
                media: { image_generation_enabled: false },
                devices: {
                  device_enabled: false,
                  device_lighting_enabled: false,
                  device_mode: "dry_run",
                  device_backend: "none",
                },
                subagent: { mode: "normal" },
                audit: { audit_mode: "minimal", audit_security_on_policy_denial: true },
                runtime: { profile: "default" },
              },
              requires_restart: false,
            }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Settings" }));

    expect(await screen.findByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(screen.queryByText("Could not load settings")).not.toBeInTheDocument();
    expect(sawFreshToken).toBe(true);
    expect(settingsCalls).toBeGreaterThanOrEqual(2);
  });

  it("keeps the selected chat open when history loading refreshes an expired API token", async () => {
    mockSessions = [
      {
        key: "websocket:chat-a",
        channel: "websocket",
        chatId: "chat-a",
        createdAt: "2026-05-18T10:00:00Z",
        updatedAt: "2026-05-18T10:00:00Z",
        preview: "Idle chat",
      },
    ];
    vi.mocked(fetchBootstrap)
      .mockResolvedValueOnce({
        token: "tok",
        ws_path: "/",
        expires_in: 300,
      })
      .mockResolvedValueOnce({
        token: "fresh-tok",
        ws_path: "/",
        expires_in: 300,
      });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("websocket%3Achat-a/webui-thread")) {
          const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
          if (auth === "Bearer tok") {
            return { ok: false, status: 401, json: async () => ({}) };
          }
          return {
            ok: true,
            status: 200,
            text: async () => JSON.stringify({
              schemaVersion: 3,
              messages: [
                { id: "u1", role: "user", content: "Recovered after idle", createdAt: 1 },
              ],
            }),
            headers: new Headers({ "content-type": "application/json" }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Idle chat" }));

    expect(await screen.findByText("Recovered after idle")).toBeInTheDocument();
    expect(screen.queryByText("What can I do for you?")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("Type your message…")).toBeInTheDocument();
    expect(fetchBootstrap).toHaveBeenCalledTimes(2);
  });

  it("returns from settings to the blank start page when no session was active", async () => {
    mockSessions = [
      {
        key: "websocket:chat-a",
        channel: "websocket",
        chatId: "chat-a",
        createdAt: "2026-04-16T10:00:00Z",
        updatedAt: "2026-04-16T10:00:00Z",
        preview: "First chat",
      },
      {
        key: "websocket:chat-b",
        channel: "websocket",
        chatId: "chat-b",
        createdAt: "2026-04-16T11:00:00Z",
        updatedAt: "2026-04-16T11:00:00Z",
        preview: "Second chat",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("/api/settings")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              agent: {
                model: "openai/gpt-4o",
                provider: "openai",
                resolved_provider: "openai",
                has_api_key: true,
              },
              providers: [{ name: "openai", label: "OpenAI", configured: true }],
              web_search: {
                provider: "duckduckgo",
                api_key_hint: null,
                base_url: null,
                providers: [
                  { name: "duckduckgo", label: "DuckDuckGo", credential: "none" },
                  { name: "brave", label: "Brave Search", credential: "api_key" },
                ],
              },
              mcp: { servers: [] },
              runtime: {
                config_path: "/tmp/config.json",
              },
              learning: {
                background_review: { enabled: false },
              },
              runtime_controls: {
                channels: { send_progress: true, send_tool_hints: false, show_reasoning: true },
                agent: {
                  unified_session: false,
                  cold_archive_enabled: true,
                  allow_agent_initiated_messages: false,
                  auxiliary_enabled: true,
                  domain_packs_enabled: true,
                  provider_retry_mode: "standard",
                  dream_annotate_line_ages: true,
                },
                learning: { background_review_enabled: false, curator_enabled: false },
                evolution: {
                  mode: "conservative",
                  allow_manual_override: false,
                  dry_run: true,
                  outcome_archive_enabled: true,
                  dependency_stale_cleanup_enabled: true,
                  auto_verify_workflows: false,
                  skill_candidates_enabled: false,
                  feedback_calibration_enabled: true,
                  sandbox_enabled: true,
                  trial_enabled: true,
                  trial_isolated_workspace: true,
                  trial_read_only_tools_only: true,
                },
                gateway: { heartbeat_enabled: true },
                security: { pairing_enabled: false, pairing_allow_self_approve: false },
                search: {
                  web_enabled: true,
                  web_fetch_use_jina_reader: true,
                  session_search_enabled: true,
                  session_search_backend: "auto",
                  session_search_semantic_enabled: true,
                  session_search_rebuild_on_start: false,
                  content_read_enabled: false,
                  content_read_use_jina_reader: true,
                },
                execution: {
                  exec_enabled: true,
                  exec_profile: "secure",
                  exec_allow_unsafe_exec: false,
                  exec_shell_syntax_policy: "restricted",
                  my_enabled: true,
                  my_allow_set: false,
                  restrict_to_workspace: false,
                },
                media: { image_generation_enabled: false },
                devices: {
                  device_enabled: false,
                  device_lighting_enabled: false,
                  device_mode: "dry_run",
                  device_backend: "none",
                },
                subagent: { mode: "normal" },
                audit: { audit_mode: "minimal", audit_security_on_policy_denial: true },
                runtime: { profile: "default" },
              },
              requires_restart: false,
            }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(document.title).toBe("OriginAgent"));

    fireEvent.click(within(sidebar).getByRole("button", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: "General" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));

    await waitFor(() => expect(document.title).toBe("OriginAgent"));
    expect(screen.getByText("What can I do for you?")).toBeInTheDocument();
  });

  it("filters sidebar sessions through the lightweight search row", async () => {
    mockSessions = [
      {
        key: "websocket:chat-alpha",
        channel: "websocket",
        chatId: "chat-alpha",
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        title: "Q2 roadmap",
        preview: "Project planning notes",
      },
      {
        key: "websocket:chat-beta",
        channel: "websocket",
        chatId: "chat-beta",
        createdAt: "2026-04-15T10:00:00Z",
        updatedAt: "2026-04-15T10:00:00Z",
        preview: "Travel ideas",
      },
    ];

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    expect(within(sidebar).getByText("Q2 roadmap")).toBeInTheDocument();
    expect(within(sidebar).getByText("Travel ideas")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "Search chats" }), {
      target: { value: "planning" },
    });

    expect(within(sidebar).getByText("Q2 roadmap")).toBeInTheDocument();
    expect(within(sidebar).queryByText("Travel ideas")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "Search chats" }), {
      target: { value: "road q2" },
    });

    expect(within(sidebar).getByText("Q2 roadmap")).toBeInTheDocument();
    expect(within(sidebar).queryByText("Travel ideas")).not.toBeInTheDocument();
  });

  it("opens a blank start page without creating an empty chat", async () => {
    mockSessions = [
      {
        key: "websocket:chat-a",
        channel: "websocket",
        chatId: "chat-a",
        createdAt: "2026-04-16T10:00:00Z",
        updatedAt: "2026-04-16T10:00:00Z",
        preview: "Existing chat",
      },
    ];

    const matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("1024px"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    vi.stubGlobal("matchMedia", matchMedia);

    const { container } = render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Toggle theme from header" }));
    expect(toggleThemeSpy).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    const desktopAside = container.querySelector("aside.lg\\:block") as HTMLElement;
    await waitFor(() => expect(desktopAside.style.width).toBe("0px"));

    expect(screen.queryByRole("button", { name: "Start a new chat" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Toggle sidebar" }));
    await waitFor(() => expect(desktopAside.style.width).toBe("272px"));

    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "New chat" }));
    expect(createChatSpy).not.toHaveBeenCalled();
    expect(screen.getByText("What can I do for you?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start a new chat" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Toggle theme from header" })).toBeInTheDocument();
    expect(within(sidebar).getByRole("button", { name: "Settings" })).toBeInTheDocument();

    expect(within(sidebar).getByText("Existing chat")).toBeInTheDocument();
  });

  it("does not flash the blank start page while the selected chat list row is refreshing", async () => {
    mockSessions = [
      {
        key: "websocket:chat-a",
        channel: "websocket",
        chatId: "chat-a",
        createdAt: "2026-04-16T10:00:00Z",
        updatedAt: "2026-04-16T10:00:00Z",
        preview: "Existing chat",
      },
    ];
    let resolveHistory:
      | ((value: { ok: boolean; status: number; json: () => Promise<unknown> }) => void)
      | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("websocket%3Achat-a/webui-thread")) {
          return new Promise((resolve) => {
            resolveHistory = resolve;
          });
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          json: async () => ({}),
        });
      }),
    );

    const { rerender } = render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Existing chat" }));
    expect(screen.getByText("Loading conversation…")).toBeInTheDocument();

    mockSessions = [];
    mockSessionsLoading = true;
    rerender(<App />);

    expect(screen.getByText("Loading conversation…")).toBeInTheDocument();
    expect(screen.queryByText("What can I do for you?")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Ask anything...")).not.toBeInTheDocument();

    await act(async () => {
      resolveHistory?.({ ok: true, status: 200, json: async () => ({ schemaVersion: 3, messages: [] }) });
    });
  });

  it("fetches provider models in settings and lets the user choose one", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/settings/provider/models")) {
          const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
          if (auth !== "Bearer tok") {
            return { ok: false, status: 401, json: async () => ({}) };
          }
          return {
            ok: true,
            status: 200,
            json: async () => ({
              provider: "openai",
              models: [
                { id: "gpt-4o-mini", owned_by: "openai" },
                { id: "gpt-4.1", owned_by: "openai" },
              ],
              source_url: "https://api.openai.com/v1/models",
              phase: "fetch",
            }),
          };
        }
        if (url.includes("/api/settings")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
              agent: {
                model: "openai/gpt-4o",
                provider: "openai",
                resolved_provider: "openai",
                has_api_key: true,
              },
              providers: [
                {
                  name: "openai",
                  label: "OpenAI",
                  configured: true,
                  api_key_hint: "open••••-key",
                  api_base: "https://api.openai.com/v1",
                  default_api_base: "https://api.openai.com/v1",
                },
              ],
              web_search: {
                provider: "duckduckgo",
                api_key_hint: null,
                base_url: null,
                providers: [{ name: "duckduckgo", label: "DuckDuckGo", credential: "none" }],
              },
              mcp: { servers: [] },
              runtime: { config_path: "/tmp/config.json" },
              learning: { background_review: { enabled: false } },
              runtime_controls: {
                channels: { send_progress: true, send_tool_hints: false, show_reasoning: true },
                agent: {
                  unified_session: false,
                  cold_archive_enabled: true,
                  allow_agent_initiated_messages: false,
                  auxiliary_enabled: true,
                  domain_packs_enabled: true,
                  provider_retry_mode: "standard",
                  dream_annotate_line_ages: true,
                },
                learning: { background_review_enabled: false, curator_enabled: false },
                evolution: {
                  mode: "conservative",
                  allow_manual_override: false,
                  dry_run: true,
                  outcome_archive_enabled: true,
                  dependency_stale_cleanup_enabled: true,
                  auto_verify_workflows: false,
                  skill_candidates_enabled: false,
                  feedback_calibration_enabled: true,
                  sandbox_enabled: true,
                  trial_enabled: true,
                  trial_isolated_workspace: true,
                  trial_read_only_tools_only: true,
                },
                gateway: { heartbeat_enabled: true },
                security: { pairing_enabled: false, pairing_allow_self_approve: false },
                search: {
                  web_enabled: true,
                  web_fetch_use_jina_reader: true,
                  session_search_enabled: true,
                  session_search_backend: "auto",
                  session_search_semantic_enabled: true,
                  session_search_rebuild_on_start: false,
                  content_read_enabled: false,
                  content_read_use_jina_reader: true,
                },
                execution: {
                  exec_enabled: true,
                  exec_profile: "secure",
                  exec_allow_unsafe_exec: false,
                  exec_shell_syntax_policy: "restricted",
                  my_enabled: true,
                  my_allow_set: false,
                  restrict_to_workspace: false,
                },
                media: { image_generation_enabled: false },
                devices: {
                  device_enabled: false,
                  device_lighting_enabled: false,
                  device_mode: "dry_run",
                  device_backend: "none",
                },
                subagent: { mode: "normal" },
                audit: { audit_mode: "minimal", audit_security_on_policy_denial: true },
                runtime: { profile: "default" },
              },
              requires_restart: false,
            }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: "General" })).toBeInTheDocument();

    const fetchButton = await screen.findByRole("button", { name: "Fetch models" });
    await user.click(fetchButton);

    const openListButton = await screen.findByRole("button", { name: "Open fetched models" });
    await user.click(openListButton);
    await user.click(await screen.findByRole("menuitem", { name: "gpt-4o-mini" }));

    await waitFor(() =>
      expect(screen.getByDisplayValue("gpt-4o-mini")).toBeInTheDocument(),
    );
  });

  it("shows a specific error when the provider does not expose a compatible models endpoint", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/settings/provider/models")) {
          return {
            ok: false,
            status: 404,
            text: async () => JSON.stringify({
              message: "All candidates failed: HTTP 404: missing",
              reason: "models_endpoint_missing",
              phase: "fetch",
            }),
          };
        }
        if (url.includes("/api/settings")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({
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
                  api_key_hint: "open••••-key",
                  api_base: "https://openrouter.ai/api/v1",
                  default_api_base: "https://openrouter.ai/api/v1",
                },
              ],
              web_search: {
                provider: "duckduckgo",
                api_key_hint: null,
                base_url: null,
                providers: [{ name: "duckduckgo", label: "DuckDuckGo", credential: "none" }],
              },
              mcp: { servers: [] },
              runtime: { config_path: "/tmp/config.json" },
              learning: { background_review: { enabled: false } },
              runtime_controls: {
                channels: { send_progress: true, send_tool_hints: false, show_reasoning: true },
                agent: {
                  unified_session: false,
                  cold_archive_enabled: true,
                  allow_agent_initiated_messages: false,
                  auxiliary_enabled: true,
                  domain_packs_enabled: true,
                  provider_retry_mode: "standard",
                  dream_annotate_line_ages: true,
                },
                learning: { background_review_enabled: false, curator_enabled: false },
                evolution: {
                  mode: "conservative",
                  allow_manual_override: false,
                  dry_run: true,
                  outcome_archive_enabled: true,
                  dependency_stale_cleanup_enabled: true,
                  auto_verify_workflows: false,
                  skill_candidates_enabled: false,
                  feedback_calibration_enabled: true,
                  sandbox_enabled: true,
                  trial_enabled: true,
                  trial_isolated_workspace: true,
                  trial_read_only_tools_only: true,
                },
                gateway: { heartbeat_enabled: true },
                security: { pairing_enabled: false, pairing_allow_self_approve: false },
                search: {
                  web_enabled: true,
                  web_fetch_use_jina_reader: true,
                  session_search_enabled: true,
                  session_search_backend: "auto",
                  session_search_semantic_enabled: true,
                  session_search_rebuild_on_start: false,
                  content_read_enabled: false,
                  content_read_use_jina_reader: true,
                },
                execution: {
                  exec_enabled: true,
                  exec_profile: "secure",
                  exec_allow_unsafe_exec: false,
                  exec_shell_syntax_policy: "restricted",
                  my_enabled: true,
                  my_allow_set: false,
                  restrict_to_workspace: false,
                },
                media: { image_generation_enabled: false },
                devices: {
                  device_enabled: false,
                  device_lighting_enabled: false,
                  device_mode: "dry_run",
                  device_backend: "none",
                },
                subagent: { mode: "normal" },
                audit: { audit_mode: "minimal", audit_security_on_policy_denial: true },
                runtime: { profile: "default" },
              },
              requires_restart: false,
            }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    render(<App />);

    await waitFor(() => expect(connectSpy).toHaveBeenCalled());
    const sidebar = screen.getByRole("navigation", { name: "Sidebar navigation" });
    fireEvent.click(within(sidebar).getByRole("button", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: "General" })).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Fetch models" }));

    expect(
      await screen.findByText("This provider endpoint does not expose a compatible model list endpoint."),
    ).toBeInTheDocument();
  });
});

import type {
  ChatSummary,
  DomainPackGovernanceResult,
  DomainPackGovernanceStats,
  DomainPackRecord,
  HomeAssistantMcpSettingsUpdate,
  McpServerSettingsUpdate,
  ProviderModelsErrorResponse,
  ProviderSettingsUpdate,
  ProviderModelsResponse,
  ReviewDecisionResult,
  ReviewProposal,
  ReviewProposalStats,
  RuntimeSettingsUpdate,
  SettingsPayload,
  SettingsUpdate,
  SelfModel,
  SkillLifecycleResult,
  SkillLifecycleStats,
  SkillRecord,
  VoiceSettingsUpdate,
  SlashCommand,
  WebSearchSettingsUpdate,
  WebuiThreadPersistedPayload,
  MetaCognitionSummary,
  OpportunitySignal,
  EvolutionStatus,
} from "./types";

export class ApiError extends Error {
  status: number;
  reason?: string;
  phase?: string;
  constructor(status: number, message: string, options?: { reason?: string; phase?: string }) {
    super(message);
    this.status = status;
    this.reason = options?.reason;
    this.phase = options?.phase;
    this.name = "ApiError";
  }
}

/** Maps technical API errors to actionable user-facing messages (U6). */
export function userFriendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    const map: Record<number, string> = {
      400: "The request was invalid. Check your input and try again.",
      401: "Authentication failed. Your session may have expired — try refreshing the page.",
      403: "Access denied. You don't have permission for this action.",
      404: "The requested resource was not found. It may have been removed.",
      413: "The uploaded file is too large. Try a smaller file.",
      429: "Too many requests. Please wait a moment and try again.",
      500: "The server encountered an error. Check the gateway logs for details.",
      502: "The server is temporarily unavailable. Try again in a few seconds.",
      503: "Service unavailable. The gateway may be starting up.",
      504: "The request timed out. Try again with a simpler query.",
    };
    return map[err.status] ?? `Unexpected error (${err.status}). Please try again.`;
  }
  if (err instanceof Error) return err.message;
  return "An unexpected error occurred. Please try again.";
}

export async function withTokenRefresh<T>(
  token: string,
  refreshToken: () => Promise<string | null>,
  action: (token: string) => Promise<T>,
): Promise<T> {
  try {
    return await action(token);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      const refreshed = await refreshToken();
      if (refreshed) return action(refreshed);
    }
    throw err;
  }
}

// -- URL helpers ----------------------------------------------------------------

type QueryValue = string | number | boolean | undefined | null;

/** Build a URL with query parameters, skipping undefined / null / empty values. */
function buildUrl(base: string, params: Record<string, QueryValue> = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    q.set(k, String(v));
  }
  const suffix = q.toString();
  return suffix ? `${base}?${suffix}` : base;
}

/** Prepend the gateway base path (empty string in production) to an API path. */
function apiUrl(path: string, base: string = ""): string {
  return `${base}${path}`;
}

// -- response parsing ----------------------------------------------------------

async function parseJsonResponse<T>(res: Response): Promise<T> {
  if (typeof res.text !== "function") {
    return (await res.json()) as T;
  }
  const text = await res.text();
  try {
    return JSON.parse(text) as T;
  } catch {
    const contentType = res.headers.get("content-type") ?? "";
    const looksLikeHtml = text.trimStart().startsWith("<");
    if (looksLikeHtml) {
      throw new ApiError(
        res.status || 500,
        "API returned HTML instead of JSON. Refresh the page or restart OriginAgent so the latest backend routes are active.",
      );
    }
    throw new ApiError(
      res.status || 500,
      contentType ? `Invalid JSON response (${contentType})` : "Invalid JSON response",
    );
  }
}

async function request<T>(
  url: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(url, {
    ...(init ?? {}),
    headers: {
      ...(init?.headers ?? {}),
      Authorization: `Bearer ${token}`,
    },
    credentials: "same-origin",
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    let reason: string | undefined;
    let phase: string | undefined;
    try {
      const text = typeof res.text === "function" ? await res.text() : "";
      if (text.trim()) {
        try {
          const parsed = JSON.parse(text) as ProviderModelsErrorResponse;
          if (parsed && typeof parsed.message === "string" && parsed.message.trim()) {
            message = parsed.message;
            reason = typeof parsed.reason === "string" ? parsed.reason : undefined;
            phase = typeof parsed.phase === "string" ? parsed.phase : undefined;
          } else {
            message = text;
          }
        } catch {
          message = text;
        }
      }
    } catch {
      // Fall back to the generic status message when the error body cannot be read.
    }
    throw new ApiError(res.status, message, { reason, phase });
  }
  return parseJsonResponse<T>(res);
}

function splitKey(key: string): { channel: string; chatId: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { channel: "", chatId: key };
  return { channel: key.slice(0, idx), chatId: key.slice(idx + 1) };
}

export async function listSessions(
  token: string,
  base: string = "",
): Promise<ChatSummary[]> {
  type Row = {
    key: string;
    created_at: string | null;
    updated_at: string | null;
    title?: string;
    preview?: string;
  };
  const body = await request<{ sessions: Row[] }>(
    apiUrl("/api/sessions", base),
    token,
  );
  return body.sessions.map((s) => ({
    key: s.key,
    ...splitKey(s.key),
    createdAt: s.created_at,
    updatedAt: s.updated_at,
    title: s.title ?? "",
    preview: s.preview ?? "",
  }));
}

/** Disk-backed WebUI display thread snapshot (separate from agent session). */
export async function fetchWebuiThread(
  token: string,
  key: string,
  base: string = "",
): Promise<WebuiThreadPersistedPayload | null> {
  const url = apiUrl(`/api/sessions/${encodeURIComponent(key)}/webui-thread`, base);
  const res = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
    credentials: "same-origin",
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return parseJsonResponse<WebuiThreadPersistedPayload>(res);
}

export async function deleteSession(
  token: string,
  key: string,
  base: string = "",
): Promise<boolean> {
  const body = await request<{ deleted: boolean }>(
    apiUrl(`/api/sessions/${encodeURIComponent(key)}/delete`, base),
    token,
  );
  return body.deleted;
}

export async function fetchSettings(
  token: string,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(apiUrl("/api/settings", base), token);
}

export async function fetchSelfModel(
  token: string,
  base: string = "",
): Promise<SelfModel> {
  const body = await request<{ self_model: SelfModel }>(apiUrl("/api/self", base), token);
  return body.self_model;
}

export async function listSlashCommands(
  token: string,
  base: string = "",
  lang: string = "",
): Promise<SlashCommand[]> {
  type Row = {
    command: string;
    title: string;
    description: string;
    icon: string;
    arg_hint?: string;
  };
  const body = await request<{ commands: Row[] }>(
    buildUrl(apiUrl("/api/commands", base), { lang: lang || undefined }),
    token,
  );
  const commands: SlashCommand[] = body.commands
    .map((command) => ({
      command: command.command,
      title: command.title,
      description: command.description,
      icon: command.icon,
      argHint: command.arg_hint ?? "",
    }));
  return commands;
}

export async function listReviewProposals(
  token: string,
  filters: { status?: string; type?: string; origin?: string; limit?: number } = {},
  base: string = "",
): Promise<{ proposals: ReviewProposal[]; stats: ReviewProposalStats }> {
  return request<{ proposals: ReviewProposal[]; stats: ReviewProposalStats }>(
    buildUrl(apiUrl("/api/reviews", base), filters),
    token,
  );
}

export async function fetchReviewProposal(
  token: string,
  proposalId: string,
  base: string = "",
): Promise<{ proposal: ReviewProposal; stats: ReviewProposalStats }> {
  return request<{ proposal: ReviewProposal; stats: ReviewProposalStats }>(
    apiUrl(`/api/reviews/${encodeURIComponent(proposalId)}`, base),
    token,
  );
}

export async function reviewProposalAction(
  token: string,
  proposalId: string,
  action: "apply" | "approve" | "reject" | "defer",
  reason = "",
  base: string = "",
): Promise<{
  result: ReviewDecisionResult;
  proposal: ReviewProposal | null;
  stats: ReviewProposalStats;
}> {
  return request<{
    result: ReviewDecisionResult;
    proposal: ReviewProposal | null;
    stats: ReviewProposalStats;
  }>(
    buildUrl(apiUrl(`/api/reviews/${encodeURIComponent(proposalId)}/${action}`, base), { reason }),
    token,
  );
}

export async function listDomains(
  token: string,
  filters: { source?: string; status?: string; limit?: number } = {},
  base: string = "",
): Promise<{ domains: DomainPackRecord[]; stats: DomainPackGovernanceStats }> {
  return request<{ domains: DomainPackRecord[]; stats: DomainPackGovernanceStats }>(
    buildUrl(apiUrl("/api/domains", base), filters),
    token,
  );
}

export async function fetchDomain(
  token: string,
  packId: string,
  base: string = "",
): Promise<{ domain: DomainPackRecord; stats: DomainPackGovernanceStats }> {
  return request<{ domain: DomainPackRecord; stats: DomainPackGovernanceStats }>(
    apiUrl(`/api/domains/${encodeURIComponent(packId)}`, base),
    token,
  );
}

export async function installDomainPack(
  token: string,
  source: string,
  reason = "",
  base: string = "",
): Promise<{ result: DomainPackGovernanceResult; domain: DomainPackRecord | null; stats: DomainPackGovernanceStats }> {
  return request<{ result: DomainPackGovernanceResult; domain: DomainPackRecord | null; stats: DomainPackGovernanceStats }>(
    buildUrl(apiUrl("/api/domains/install", base), { source, reason }),
    token,
  );
}

export async function domainPackAction(
  token: string,
  packId: string,
  action: "upgrade" | "enable" | "disable" | "activate" | "deactivate" | "uninstall" | "eval",
  options: { source?: string; reason?: string } = {},
  base: string = "",
): Promise<{ result: DomainPackGovernanceResult; domain: DomainPackRecord | null; stats: DomainPackGovernanceStats }> {
  return request<{ result: DomainPackGovernanceResult; domain: DomainPackRecord | null; stats: DomainPackGovernanceStats }>(
    buildUrl(apiUrl(`/api/domains/${encodeURIComponent(packId)}/${action}`, base), options),
    token,
  );
}

export async function listSkills(
  token: string,
  filters: { source?: string; status?: string; limit?: number } = {},
  base: string = "",
): Promise<{ skills: SkillRecord[]; stats: SkillLifecycleStats }> {
  return request<{ skills: SkillRecord[]; stats: SkillLifecycleStats }>(
    buildUrl(apiUrl("/api/skills", base), filters),
    token,
  );
}

export async function fetchSkill(
  token: string,
  skillName: string,
  base: string = "",
): Promise<{ skill: SkillRecord; stats: SkillLifecycleStats }> {
  return request<{ skill: SkillRecord; stats: SkillLifecycleStats }>(
    apiUrl(`/api/skills/${encodeURIComponent(skillName)}`, base),
    token,
  );
}

export async function skillLifecycleAction(
  token: string,
  skillName: string,
  action: "verify" | "activate" | "deprecate" | "reject" | "always",
  options: { reason?: string; enabled?: boolean } = {},
  base: string = "",
): Promise<{
  result: SkillLifecycleResult;
  skill: SkillRecord | null;
  stats: SkillLifecycleStats;
}> {
  return request<{
    result: SkillLifecycleResult;
    skill: SkillRecord | null;
    stats: SkillLifecycleStats;
  }>(
    buildUrl(
      apiUrl(`/api/skills/${encodeURIComponent(skillName)}/${action}`, base),
      options as Record<string, QueryValue>,
    ),
    token,
  );
}

export async function updateSettings(
  token: string,
  update: SettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/update", base), update as Record<string, QueryValue>),
    token,
  );
}

export async function updateProviderSettings(
  token: string,
  update: ProviderSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/provider/update", base), {
      provider: update.provider,
      api_key: update.apiKey,
      api_base: update.apiBase,
    }),
    token,
  );
}

export async function fetchProviderModels(
  token: string,
  update: ProviderSettingsUpdate,
  base: string = "",
): Promise<ProviderModelsResponse> {
  return request<ProviderModelsResponse>(
    buildUrl(apiUrl("/api/settings/provider/models", base), {
      provider: update.provider,
      api_key: update.apiKey,
      api_base: update.apiBase,
      force_refresh: update.forceRefresh,
    }),
    token,
  );
}

export async function updateWebSearchSettings(
  token: string,
  update: WebSearchSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/web-search/update", base), {
      provider: update.provider,
      api_key: update.apiKey,
      base_url: update.baseUrl,
    }),
    token,
  );
}

export async function updateBackgroundReviewSettings(
  token: string,
  enabled: boolean,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/learning/background-review/update", base), { enabled }),
    token,
  );
}

export async function updateRuntimeSettings(
  token: string,
  update: RuntimeSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/runtime/update", base), { config: JSON.stringify(update) }),
    token,
  );
}

export async function updateVoiceSettings(
  token: string,
  update: VoiceSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/local-awareness/audio/update", base), { config: JSON.stringify(update) }),
    token,
  );
}

export async function upsertMcpServerSettings(
  token: string,
  update: McpServerSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/mcp/upsert", base), { config: JSON.stringify(update) }),
    token,
  );
}

export async function upsertHomeAssistantMcpSettings(
  token: string,
  update: HomeAssistantMcpSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/mcp/home-assistant/upsert", base), {
      name: update.name,
      address: update.address,
      token: update.token,
    }),
    token,
  );
}

export async function deleteMcpServerSettings(
  token: string,
  name: string,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(
    buildUrl(apiUrl("/api/settings/mcp/delete", base), { name }),
    token,
  );
}

export async function fetchMetaCognitionSummary(
  token: string,
  base: string = "",
): Promise<MetaCognitionSummary> {
  return request<MetaCognitionSummary>(
    apiUrl("/api/cognition/status", base),
    token,
  );
}

export async function listSignals(
  token: string,
  filters: { status?: string; kind?: string; limit?: number } = {},
  base: string = "",
): Promise<{ signals: OpportunitySignal[]; count: number }> {
  return request<{ signals: OpportunitySignal[]; count: number }>(
    buildUrl(apiUrl("/api/evolution/signals", base), filters),
    token,
  );
}

export async function updateSignal(
  token: string,
  signalId: string,
  action: "suppress" | "resume",
  options: { reason?: string } = {},
  base: string = "",
): Promise<{ ok: boolean; signal: OpportunitySignal | null }> {
  return request<{ ok: boolean; signal: OpportunitySignal | null }>(
    buildUrl(apiUrl(`/api/evolution/signals/${encodeURIComponent(signalId)}/${action}`, base), options),
    token,
  );
}

export async function fetchEvolutionStatus(
  token: string,
  base: string = "",
): Promise<EvolutionStatus> {
  return request<EvolutionStatus>(
    apiUrl("/api/evolution/status", base),
    token,
  );
}

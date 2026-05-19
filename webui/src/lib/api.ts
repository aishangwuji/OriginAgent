import type {
  ChatSummary,
  HomeAssistantMcpSettingsUpdate,
  McpServerSettingsUpdate,
  ProviderSettingsUpdate,
  ReviewDecisionResult,
  ReviewProposal,
  ReviewProposalStats,
  SettingsPayload,
  SettingsUpdate,
  SkillLifecycleResult,
  SkillLifecycleStats,
  SkillRecord,
  SlashCommand,
  WebSearchSettingsUpdate,
  WebuiThreadPersistedPayload,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
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
        "API returned HTML instead of JSON. Refresh the page or restart OpenHome so the latest backend routes are active.",
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
    throw new ApiError(res.status, `HTTP ${res.status}`);
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
    `${base}/api/sessions`,
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
  const url = `${base}/api/sessions/${encodeURIComponent(key)}/webui-thread`;
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
    `${base}/api/sessions/${encodeURIComponent(key)}/delete`,
    token,
  );
  return body.deleted;
}

export async function fetchSettings(
  token: string,
  base: string = "",
): Promise<SettingsPayload> {
  return request<SettingsPayload>(`${base}/api/settings`, token);
}

export async function listSlashCommands(
  token: string,
  base: string = "",
): Promise<SlashCommand[]> {
  type Row = {
    command: string;
    title: string;
    description: string;
    icon: string;
    arg_hint?: string;
  };
  const body = await request<{ commands: Row[] }>(`${base}/api/commands`, token);
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
  filters: { status?: string; type?: string; limit?: number } = {},
  base: string = "",
): Promise<{ proposals: ReviewProposal[]; stats: ReviewProposalStats }> {
  const query = new URLSearchParams();
  if (filters.status) query.set("status", filters.status);
  if (filters.type) query.set("type", filters.type);
  if (filters.limit !== undefined) query.set("limit", String(filters.limit));
  const suffix = query.toString() ? `?${query}` : "";
  return request<{ proposals: ReviewProposal[]; stats: ReviewProposalStats }>(
    `${base}/api/reviews${suffix}`,
    token,
  );
}

export async function fetchReviewProposal(
  token: string,
  proposalId: string,
  base: string = "",
): Promise<{ proposal: ReviewProposal; stats: ReviewProposalStats }> {
  return request<{ proposal: ReviewProposal; stats: ReviewProposalStats }>(
    `${base}/api/reviews/${encodeURIComponent(proposalId)}`,
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
  const query = new URLSearchParams();
  if (reason.trim()) query.set("reason", reason.trim());
  const suffix = query.toString() ? `?${query}` : "";
  return request<{
    result: ReviewDecisionResult;
    proposal: ReviewProposal | null;
    stats: ReviewProposalStats;
  }>(
    `${base}/api/reviews/${encodeURIComponent(proposalId)}/${action}${suffix}`,
    token,
  );
}

export async function listSkills(
  token: string,
  filters: { source?: string; status?: string; limit?: number } = {},
  base: string = "",
): Promise<{ skills: SkillRecord[]; stats: SkillLifecycleStats }> {
  const query = new URLSearchParams();
  if (filters.source) query.set("source", filters.source);
  if (filters.status) query.set("status", filters.status);
  if (filters.limit !== undefined) query.set("limit", String(filters.limit));
  const suffix = query.toString() ? `?${query}` : "";
  return request<{ skills: SkillRecord[]; stats: SkillLifecycleStats }>(
    `${base}/api/skills${suffix}`,
    token,
  );
}

export async function fetchSkill(
  token: string,
  skillName: string,
  base: string = "",
): Promise<{ skill: SkillRecord; stats: SkillLifecycleStats }> {
  return request<{ skill: SkillRecord; stats: SkillLifecycleStats }>(
    `${base}/api/skills/${encodeURIComponent(skillName)}`,
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
  const query = new URLSearchParams();
  if (options.reason?.trim()) query.set("reason", options.reason.trim());
  if (options.enabled !== undefined) query.set("enabled", options.enabled ? "true" : "false");
  const suffix = query.toString() ? `?${query}` : "";
  return request<{
    result: SkillLifecycleResult;
    skill: SkillRecord | null;
    stats: SkillLifecycleStats;
  }>(
    `${base}/api/skills/${encodeURIComponent(skillName)}/${action}${suffix}`,
    token,
  );
}

export async function updateSettings(
  token: string,
  update: SettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  if (update.model !== undefined) query.set("model", update.model);
  if (update.provider !== undefined) query.set("provider", update.provider);
  return request<SettingsPayload>(`${base}/api/settings/update?${query}`, token);
}

export async function updateProviderSettings(
  token: string,
  update: ProviderSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("provider", update.provider);
  if (update.apiKey !== undefined) query.set("api_key", update.apiKey);
  if (update.apiBase !== undefined) query.set("api_base", update.apiBase);
  return request<SettingsPayload>(
    `${base}/api/settings/provider/update?${query}`,
    token,
  );
}

export async function updateWebSearchSettings(
  token: string,
  update: WebSearchSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("provider", update.provider);
  if (update.apiKey !== undefined) query.set("api_key", update.apiKey);
  if (update.baseUrl !== undefined) query.set("base_url", update.baseUrl);
  return request<SettingsPayload>(
    `${base}/api/settings/web-search/update?${query}`,
    token,
  );
}

export async function updateBackgroundReviewSettings(
  token: string,
  enabled: boolean,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("enabled", enabled ? "true" : "false");
  return request<SettingsPayload>(
    `${base}/api/settings/learning/background-review/update?${query}`,
    token,
  );
}

export async function upsertMcpServerSettings(
  token: string,
  update: McpServerSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("config", JSON.stringify(update));
  return request<SettingsPayload>(
    `${base}/api/settings/mcp/upsert?${query}`,
    token,
  );
}

export async function upsertHomeAssistantMcpSettings(
  token: string,
  update: HomeAssistantMcpSettingsUpdate,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("name", update.name);
  query.set("address", update.address);
  if (update.token !== undefined) query.set("token", update.token);
  return request<SettingsPayload>(
    `${base}/api/settings/mcp/home-assistant/upsert?${query}`,
    token,
  );
}

export async function deleteMcpServerSettings(
  token: string,
  name: string,
  base: string = "",
): Promise<SettingsPayload> {
  const query = new URLSearchParams();
  query.set("name", name);
  return request<SettingsPayload>(
    `${base}/api/settings/mcp/delete?${query}`,
    token,
  );
}

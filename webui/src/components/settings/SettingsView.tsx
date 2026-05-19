import { useCallback, useEffect, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import {
  Bot,
  Brain,
  ChevronLeft,
  ChevronDown,
  Check,
  Cloud,
  Cpu,
  Database,
  Plus,
  Server,
  Trash2,
  Eye,
  EyeOff,
  Pencil,
  Gem,
  Grid3X3,
  GraduationCap,
  Hexagon,
  Loader2,
  LogOut,
  KeyRound,
  Layers,
  Moon,
  Orbit,
  RotateCcw,
  Settings,
  Sparkles,
  Triangle,
  Waves,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  deleteMcpServerSettings,
  fetchSettings,
  fetchSkill,
  listSkills,
  skillLifecycleAction,
  updateBackgroundReviewSettings,
  updateProviderSettings,
  updateSettings,
  updateWebSearchSettings,
  upsertHomeAssistantMcpSettings,
  upsertMcpServerSettings,
  withTokenRefresh,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";
import type {
  McpServerSettings,
  McpServerSettingsUpdate,
  McpTransportType,
  SettingsPayload,
  SkillLifecycleStats,
  SkillRecord,
  WebSearchSettingsUpdate,
} from "@/lib/types";

type SettingsSectionKey = "general" | "byok" | "skills" | "mcp";
type ByokPaneKey = "llm" | "web-search";
type McpFormState = {
  name: string;
  type: McpTransportType;
  command: string;
  args: string;
  env: string;
  url: string;
  headers: string;
  toolTimeout: string;
  enabledTools: string;
};
type HomeAssistantMcpFormState = {
  name: string;
  address: string;
  token: string;
};
const SETTINGS_LOAD_RETRY_DELAYS_MS = [350, 900, 1600] as const;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function fetchSettingsWithRetry(
  token: string,
  refreshToken: () => Promise<string | null>,
): Promise<SettingsPayload> {
  let lastError: unknown;
  for (let attempt = 0; attempt <= SETTINGS_LOAD_RETRY_DELAYS_MS.length; attempt += 1) {
    try {
      return await withTokenRefresh(token, refreshToken, fetchSettings);
    } catch (err) {
      lastError = err;
      const delay = SETTINGS_LOAD_RETRY_DELAYS_MS[attempt];
      if (delay === undefined) break;
      await sleep(delay);
    }
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

interface SettingsViewProps {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onBackToChat: () => void;
  onModelNameChange: (modelName: string | null) => void;
  onLogout?: () => void;
  onRestart?: () => void;
  isRestarting?: boolean;
}

export function SettingsView({
  theme,
  onToggleTheme,
  onBackToChat,
  onModelNameChange,
  onLogout,
  onRestart,
  isRestarting = false,
}: SettingsViewProps) {
  const { t } = useTranslation();
  const { token, refreshToken } = useClient();
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [providerSaving, setProviderSaving] = useState<string | null>(null);
  const [webSearchSaving, setWebSearchSaving] = useState(false);
  const [backgroundReviewSaving, setBackgroundReviewSaving] = useState(false);
  const [mcpSaving, setMcpSaving] = useState<string | null>(null);
  const [mcpDeleting, setMcpDeleting] = useState<string | null>(null);
  const [homeAssistantMcpSaving, setHomeAssistantMcpSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<SettingsSectionKey>("general");
  const [expandedProvider, setExpandedProvider] = useState<string | null>(null);
  const [providerForms, setProviderForms] = useState<Record<string, { apiKey: string; apiBase: string }>>({});
  const [visibleProviderKeys, setVisibleProviderKeys] = useState<Record<string, boolean>>({});
  const [editingProviderKeys, setEditingProviderKeys] = useState<Record<string, boolean>>({});
  const [webSearchForm, setWebSearchForm] = useState<WebSearchSettingsUpdate>({
    provider: "duckduckgo",
    apiKey: "",
    baseUrl: "",
  });
  const [webSearchKeyVisible, setWebSearchKeyVisible] = useState(false);
  const [webSearchKeyEditing, setWebSearchKeyEditing] = useState(false);
  const [form, setForm] = useState({
    model: "",
    provider: "",
  });
  const [mcpEditing, setMcpEditing] = useState<string | null>(null);
  const [mcpForms, setMcpForms] = useState<Record<string, McpFormState>>({});
  const [homeAssistantMcpForm, setHomeAssistantMcpForm] = useState<HomeAssistantMcpFormState>({
    name: "home_assistant",
    address: "http://localhost:8123",
    token: "",
  });

  const applyPayload = useCallback((payload: SettingsPayload) => {
    setSettings(payload);
    setForm({
      model: payload.agent.model,
      provider: payload.agent.provider,
    });
    setWebSearchForm((prev) => ({
      provider: payload.web_search.provider,
      apiKey: prev.provider === payload.web_search.provider ? prev.apiKey ?? "" : "",
      baseUrl: payload.web_search.base_url ?? "",
    }));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSettingsWithRetry(token, refreshToken)
      .then((payload) => {
        if (!cancelled) {
          applyPayload(payload);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applyPayload, refreshToken, token]);

  useEffect(() => {
    if (!settings) return;
    setProviderForms((prev) => {
      const next = { ...prev };
      for (const provider of settings.providers) {
        next[provider.name] = {
          apiKey: next[provider.name]?.apiKey ?? "",
          apiBase: next[provider.name]?.apiBase ?? provider.api_base ?? provider.default_api_base ?? "",
        };
      }
      return next;
    });
  }, [settings]);

  const dirty = useMemo(() => {
    if (!settings) return false;
    return (
      form.model !== settings.agent.model ||
      form.provider !== settings.agent.provider
    );
  }, [form, settings]);

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      const update = {
        model: form.model,
        ...(form.provider ? { provider: form.provider } : {}),
      };
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        updateSettings(freshToken, update),
      );
      applyPayload(payload);
      onModelNameChange(payload.agent.model || null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const saveProvider = async (providerName: string) => {
    if (providerSaving) return;
    const provider = settings?.providers.find((item) => item.name === providerName);
    if (!provider) return;
    const providerForm = providerForms[providerName] ?? { apiKey: "", apiBase: "" };
    const apiKey = providerForm.apiKey.trim();
    if (!provider.configured && !apiKey) {
      setError(t("settings.byok.apiKeyRequired"));
      return;
    }
    setProviderSaving(providerName);
    try {
      const update = {
        provider: providerName,
        apiKey: apiKey || undefined,
        apiBase: providerForm.apiBase.trim(),
      };
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        updateProviderSettings(freshToken, update),
      );
      applyPayload(payload);
      setProviderForms((prev) => ({
        ...prev,
        [providerName]: {
          apiKey: "",
          apiBase: providerForm.apiBase.trim(),
        },
      }));
      setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: false }));
      setEditingProviderKeys((prev) => ({ ...prev, [providerName]: false }));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setProviderSaving(null);
    }
  };

  const saveWebSearch = async () => {
    if (!settings || webSearchSaving) return;
    const provider = settings.web_search.providers.find((item) => item.name === webSearchForm.provider);
    if (!provider) return;
    const apiKey = webSearchForm.apiKey?.trim() ?? "";
    const baseUrl = webSearchForm.baseUrl?.trim() ?? "";
    const hasExistingSecret =
      provider.credential === "api_key" &&
      webSearchForm.provider === settings.web_search.provider &&
      !!settings.web_search.api_key_hint;

    if (provider.credential === "api_key" && !apiKey && !hasExistingSecret) {
      setError(t("settings.byok.webSearch.apiKeyRequired"));
      return;
    }
    if (provider.credential === "base_url" && !baseUrl) {
      setError(t("settings.byok.webSearch.baseUrlRequired"));
      return;
    }

    setWebSearchSaving(true);
    try {
      const update: WebSearchSettingsUpdate = { provider: webSearchForm.provider };
      if (provider.credential === "api_key" && apiKey) update.apiKey = apiKey;
      if (provider.credential === "base_url") update.baseUrl = baseUrl;
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        updateWebSearchSettings(freshToken, update),
      );
      applyPayload(payload);
      setWebSearchForm((prev) => ({
        provider: payload.web_search.provider,
        apiKey: "",
        baseUrl: payload.web_search.base_url ?? prev.baseUrl ?? "",
      }));
      setWebSearchKeyVisible(false);
      setWebSearchKeyEditing(false);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setWebSearchSaving(false);
    }
  };

  const toggleBackgroundReview = async (enabled: boolean) => {
    if (!settings || backgroundReviewSaving) return;
    setBackgroundReviewSaving(true);
    try {
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        updateBackgroundReviewSettings(freshToken, enabled),
      );
      applyPayload(payload);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBackgroundReviewSaving(false);
    }
  };

  const resetProviderDraft = useCallback((providerName: string) => {
    const provider = settings?.providers.find((item) => item.name === providerName);
    if (!provider) return;
    setProviderForms((prev) => ({
      ...prev,
      [providerName]: {
        apiKey: "",
        apiBase: provider.api_base ?? provider.default_api_base ?? "",
      },
    }));
    setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: false }));
    setEditingProviderKeys((prev) => ({ ...prev, [providerName]: false }));
  }, [settings]);

  const handleToggleProvider = useCallback((providerName: string) => {
    if (expandedProvider) resetProviderDraft(expandedProvider);
    setExpandedProvider(expandedProvider === providerName ? null : providerName);
  }, [expandedProvider, resetProviderDraft]);

  const resetWebSearchDraft = useCallback(() => {
    if (!settings) return;
    setWebSearchForm({
      provider: settings.web_search.provider,
      apiKey: "",
      baseUrl: settings.web_search.base_url ?? "",
    });
    setWebSearchKeyVisible(false);
    setWebSearchKeyEditing(false);
  }, [settings]);

  const handleWebSearchProviderChange = useCallback((provider: string) => {
    if (!settings) return;
    setWebSearchForm({
      provider,
      apiKey: "",
      baseUrl: provider === settings.web_search.provider ? settings.web_search.base_url ?? "" : "",
    });
    setWebSearchKeyVisible(false);
    setWebSearchKeyEditing(false);
  }, [settings]);

  const toggleProviderKeyVisibility = (providerName: string) => {
    const isVisible = visibleProviderKeys[providerName];
    setVisibleProviderKeys((prev) => ({ ...prev, [providerName]: !isVisible }));
  };

  const toggleProviderKeyEditing = (providerName: string) => {
    setEditingProviderKeys((prev) => {
      const nextEditing = !prev[providerName];
      if (!nextEditing) {
        setProviderForms((forms) => ({
          ...forms,
          [providerName]: {
            apiKey: "",
            apiBase: forms[providerName]?.apiBase ?? "",
          },
        }));
        setVisibleProviderKeys((visible) => ({ ...visible, [providerName]: false }));
      }
      return { ...prev, [providerName]: nextEditing };
    });
  };

  const startMcpAdd = () => {
    setMcpEditing("__new__");
    setMcpForms((prev) => ({
      ...prev,
      __new__: createEmptyMcpForm(),
    }));
  };

  const startMcpEdit = (server: McpServerSettings) => {
    setMcpEditing(server.name);
    setMcpForms((prev) => ({
      ...prev,
      [server.name]: formFromMcpServer(server),
    }));
  };

  const cancelMcpEdit = (key: string) => {
    setMcpEditing(null);
    setMcpForms((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const saveMcpServer = async (key: string) => {
    if (!settings || mcpSaving) return;
    const formState = mcpForms[key];
    if (!formState) return;
    const update = mcpUpdateFromForm(formState);
    if (!update.name) {
      setError(t("settings.mcp.validation.nameRequired"));
      return;
    }
    if (update.type === "stdio" && !update.command?.trim()) {
      setError(t("settings.mcp.validation.commandRequired"));
      return;
    }
    if (update.type !== "stdio" && !update.url?.trim()) {
      setError(t("settings.mcp.validation.urlRequired"));
      return;
    }
    setMcpSaving(key);
    try {
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        upsertMcpServerSettings(freshToken, update),
      );
      applyPayload(payload);
      setMcpEditing(null);
      setMcpForms((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setMcpSaving(null);
    }
  };

  const saveHomeAssistantMcp = async () => {
    if (!settings || homeAssistantMcpSaving) return;
    const name = homeAssistantMcpForm.name.trim();
    const address = homeAssistantMcpForm.address.trim();
    const tokenValue = homeAssistantMcpForm.token.trim();
    const existing = settings.mcp?.servers.find((server) => server.name === name);
    const hasExistingToken = !!existing?.headers?.Authorization;
    if (!name) {
      setError(t("settings.mcp.validation.nameRequired"));
      return;
    }
    if (!address) {
      setError(t("settings.mcp.homeAssistant.validation.addressRequired"));
      return;
    }
    if (!tokenValue && !hasExistingToken) {
      setError(t("settings.mcp.homeAssistant.validation.tokenRequired"));
      return;
    }
    setHomeAssistantMcpSaving(true);
    try {
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        upsertHomeAssistantMcpSettings(freshToken, {
          name,
          address,
          token: tokenValue || undefined,
        }),
      );
      applyPayload(payload);
      setHomeAssistantMcpForm((prev) => ({
        ...prev,
        name,
        address,
        token: "",
      }));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setHomeAssistantMcpSaving(false);
    }
  };

  const deleteMcpServer = async (name: string) => {
    if (!settings || mcpDeleting) return;
    setMcpDeleting(name);
    try {
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        deleteMcpServerSettings(freshToken, name),
      );
      applyPayload(payload);
      if (mcpEditing === name) setMcpEditing(null);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setMcpDeleting(null);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden bg-[radial-gradient(circle_at_50%_0%,hsl(var(--muted))_0%,hsl(var(--background))_42%)]">
      <SettingsSidebar
        activeSection={activeSection}
        onSelectSection={setActiveSection}
        onBackToChat={onBackToChat}
        onLogout={onLogout}
      />

      <main className="min-w-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
        <div className="mx-auto w-full max-w-[840px] px-6 py-10 sm:px-10 lg:py-14">
          <div className="mb-8">
            <p className="mb-2 text-[13px] font-medium text-muted-foreground">
              {t("settings.sidebar.title")}
            </p>
            <h1 className="text-[28px] font-semibold leading-tight tracking-[-0.035em] text-foreground sm:text-[34px]">
              {t(`settings.nav.${activeSection}`)}
            </h1>
          </div>

          {loading ? (
            <div className="flex h-48 items-center justify-center rounded-[24px] border border-border/50 bg-card/75 text-sm text-muted-foreground shadow-[0_20px_70px_rgba(15,23,42,0.07)]">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("settings.status.loading")}
            </div>
          ) : error && !settings ? (
            <SettingsGroup>
              <SettingsRow title={t("settings.status.loadError")}>
                <span className="max-w-[520px] text-sm text-muted-foreground">{error}</span>
              </SettingsRow>
            </SettingsGroup>
          ) : settings ? (
            <div className="space-y-5">
              {error ? (
                <div className="rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
                  {error}
                </div>
              ) : null}
              {activeSection === "general" ? (
                <GeneralSettings
                  theme={theme}
                  onToggleTheme={onToggleTheme}
                  form={form}
                  setForm={setForm}
                  settings={settings}
                  dirty={dirty}
                  saving={saving}
                  onSave={save}
                  onRestart={onRestart}
                  isRestarting={isRestarting}
                  onOpenByok={() => setActiveSection("byok")}
                  backgroundReviewSaving={backgroundReviewSaving}
                  onToggleBackgroundReview={toggleBackgroundReview}
                />
              ) : activeSection === "skills" ? (
                <SkillsSettings />
              ) : activeSection === "byok" ? (
                <ByokSettings
                  settings={settings}
                  expandedProvider={expandedProvider}
                  providerForms={providerForms}
                  visibleProviderKeys={visibleProviderKeys}
                  editingProviderKeys={editingProviderKeys}
                  providerSaving={providerSaving}
                  webSearchForm={webSearchForm}
                  webSearchKeyVisible={webSearchKeyVisible}
                  webSearchKeyEditing={webSearchKeyEditing}
                  webSearchSaving={webSearchSaving}
                  onToggleProvider={handleToggleProvider}
                  onToggleProviderKey={toggleProviderKeyVisibility}
                  onToggleProviderKeyEditing={toggleProviderKeyEditing}
                  onChangeProviderForm={(provider, value) =>
                    setProviderForms((prev) => ({
                      ...prev,
                      [provider]: {
                        apiKey: prev[provider]?.apiKey ?? "",
                        apiBase: prev[provider]?.apiBase ?? "",
                        ...value,
                      },
                    }))
                  }
                  onSaveProvider={saveProvider}
                  onChangeWebSearchForm={setWebSearchForm}
                  onChangeWebSearchProvider={handleWebSearchProviderChange}
                  onToggleWebSearchKey={() => setWebSearchKeyVisible((visible) => !visible)}
                  onToggleWebSearchKeyEditing={() => {
                    setWebSearchKeyEditing((editing) => !editing);
                    setWebSearchKeyVisible(false);
                    setWebSearchForm((prev) => ({ ...prev, apiKey: "" }));
                  }}
                  onResetProviderDraft={resetProviderDraft}
                  onResetWebSearchDraft={resetWebSearchDraft}
                  onSaveWebSearch={saveWebSearch}
                />
              ) : (
                <McpSettings
                  settings={settings}
                  editingKey={mcpEditing}
                  forms={mcpForms}
                  savingKey={mcpSaving}
                  deletingKey={mcpDeleting}
                  homeAssistantForm={homeAssistantMcpForm}
                  homeAssistantSaving={homeAssistantMcpSaving}
                  onChangeHomeAssistantForm={(value) =>
                    setHomeAssistantMcpForm((prev) => ({ ...prev, ...value }))
                  }
                  onSaveHomeAssistant={saveHomeAssistantMcp}
                  onAdd={startMcpAdd}
                  onEdit={startMcpEdit}
                  onCancel={cancelMcpEdit}
                  onSave={saveMcpServer}
                  onDelete={deleteMcpServer}
                  onChangeForm={(key, value) =>
                    setMcpForms((prev) => ({
                      ...prev,
                      [key]: {
                        ...(prev[key] ?? createEmptyMcpForm()),
                        ...value,
                      },
                    }))
                  }
                />
              )}
            </div>
          ) : null}
        </div>
      </main>
    </div>
  );
}

const SETTINGS_NAV_ITEMS = [
  { key: "general", icon: Settings },
  { key: "byok", icon: KeyRound },
  { key: "skills", icon: GraduationCap },
  { key: "mcp", icon: Server },
] as const;

function SettingsSidebar({
  activeSection,
  onSelectSection,
  onBackToChat,
  onLogout,
}: {
  activeSection: SettingsSectionKey;
  onSelectSection: (section: SettingsSectionKey) => void;
  onBackToChat: () => void;
  onLogout?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <aside className="flex w-[17rem] shrink-0 flex-col border-r border-border/55 bg-card/62 px-3 py-4 shadow-[inset_-1px_0_0_rgba(255,255,255,0.55)] backdrop-blur-xl dark:bg-card/45 dark:shadow-none">
      <button
        type="button"
        onClick={onBackToChat}
        className="mb-3 inline-flex w-fit items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground"
      >
        <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
        {t("settings.backToChat")}
      </button>
      <div className="mb-5 px-2">
        <h2 className="text-[21px] font-semibold tracking-[-0.035em] text-foreground">
          {t("settings.sidebar.title")}
        </h2>
      </div>

      <nav aria-label={t("settings.sidebar.ariaLabel")} className="space-y-1">
        {SETTINGS_NAV_ITEMS.map(({ key, icon: Icon }) => {
          const active = key === activeSection;
          return (
            <button
              key={key}
              type="button"
              aria-current={active ? "page" : undefined}
              onClick={() => onSelectSection(key)}
              className={cn(
                "flex h-9 w-full items-center gap-2 rounded-[10px] px-2.5 text-left text-[13px] font-medium transition-colors",
                active
                  ? "bg-muted/90 text-foreground shadow-[inset_0_0_0_1px_rgba(0,0,0,0.025)]"
                  : "text-muted-foreground/78 hover:bg-muted/45 hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" strokeWidth={2} aria-hidden />
              <span className="truncate">{t(`settings.nav.${key}`)}</span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto pt-4">
        {onLogout ? (
          <Button
            type="button"
            variant="ghost"
            onClick={onLogout}
            className="h-9 w-full justify-start gap-2 rounded-[10px] px-2.5 text-[13px] font-medium text-muted-foreground hover:bg-destructive/8 hover:text-destructive"
          >
            <LogOut className="h-4 w-4" aria-hidden />
            {t("app.account.logout")}
          </Button>
        ) : null}
      </div>
    </aside>
  );
}

function GeneralSettings({
  theme,
  onToggleTheme,
  form,
  setForm,
  settings,
  dirty,
  saving,
  onSave,
  onRestart,
  isRestarting,
  onOpenByok,
  backgroundReviewSaving,
  onToggleBackgroundReview,
}: {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  form: {
    model: string;
    provider: string;
  };
  setForm: Dispatch<SetStateAction<{
    model: string;
    provider: string;
  }>>;
  settings: SettingsPayload;
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onRestart?: () => void;
  isRestarting?: boolean;
  onOpenByok: () => void;
  backgroundReviewSaving: boolean;
  onToggleBackgroundReview: (enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  const configuredProviders = settings.providers.filter((provider) => provider.configured);
  const providerValue = configuredProviders.some((provider) => provider.name === form.provider)
    ? form.provider
    : "";
  return (
    <div className="space-y-8">
      <section>
        <SettingsSectionTitle>{t("settings.sections.interface")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.theme")}
            description={t("settings.help.theme")}
          >
            <button
              type="button"
              onClick={onToggleTheme}
              className="inline-flex h-8 items-center rounded-full bg-muted p-0.5 text-[12px] font-medium text-muted-foreground"
            >
              <span
                className={cn(
                  "rounded-full px-3 py-1 transition-colors",
                  theme === "light" && "bg-background text-foreground shadow-sm",
                )}
              >
                {t("settings.values.light")}
              </span>
              <span
                className={cn(
                  "rounded-full px-3 py-1 transition-colors",
                  theme === "dark" && "bg-background text-foreground shadow-sm",
                )}
              >
                {t("settings.values.dark")}
              </span>
            </button>
          </SettingsRow>

          <SettingsRow
            title={t("settings.rows.language")}
            description={t("settings.help.language")}
          >
            <LanguageSwitcher />
          </SettingsRow>
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{t("settings.sections.ai")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.provider")}
            description={t("settings.help.provider")}
          >
            <ProviderPicker
              providers={configuredProviders}
              value={providerValue}
              emptyLabel={t("settings.byok.noConfiguredProviders")}
              onChange={(provider) => setForm((prev) => ({ ...prev, provider }))}
            />
          </SettingsRow>

          <SettingsRow
            title={t("settings.rows.model")}
            description={t("settings.help.model")}
          >
            <Input
              value={form.model}
              onChange={(event) => setForm((prev) => ({ ...prev, model: event.target.value }))}
              className="h-8 w-[280px] rounded-full text-[13px]"
            />
          </SettingsRow>

          {(dirty || saving || settings.requires_restart) ? (
            <SettingsFooter
              dirty={dirty}
              saving={saving}
              saved={settings.requires_restart && !dirty}
              onSave={onSave}
            />
          ) : null}
          {configuredProviders.length === 0 ? (
            <SettingsRow title={t("settings.byok.configureFirst")}>
              <Button size="sm" variant="outline" onClick={onOpenByok} className="rounded-full">
                {t("settings.byok.openByok")}
              </Button>
            </SettingsRow>
          ) : null}
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{t("settings.sections.learning")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.backgroundReview")}
            description={t("settings.help.backgroundReview")}
          >
            <button
              type="button"
              role="switch"
              aria-label={t("settings.rows.backgroundReview")}
              aria-checked={settings.learning.background_review.enabled}
              disabled={backgroundReviewSaving}
              onClick={() => onToggleBackgroundReview(!settings.learning.background_review.enabled)}
              className={cn(
                "inline-flex h-7 w-12 items-center rounded-full p-0.5 transition-colors",
                settings.learning.background_review.enabled ? "bg-primary" : "bg-muted",
                backgroundReviewSaving && "opacity-60",
              )}
            >
              <span
                className={cn(
                  "h-6 w-6 rounded-full bg-background shadow-sm transition-transform",
                  settings.learning.background_review.enabled && "translate-x-5",
                )}
              />
            </button>
          </SettingsRow>
        </SettingsGroup>
      </section>

      {onRestart && (
        <section>
          <SettingsSectionTitle>{t("settings.sections.system")}</SettingsSectionTitle>
          <SettingsGroup>
            <SettingsRow
              title={t("settings.rows.restart")}
              description={t("app.system.restartHint")}
            >
              <Button
                size="sm"
                variant="outline"
                onClick={onRestart}
                disabled={isRestarting}
                className="rounded-full"
              >
                {isRestarting ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                ) : (
                  <RotateCcw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                )}
                {isRestarting ? t("app.system.restarting") : t("app.system.restart")}
              </Button>
            </SettingsRow>
            <SettingsRow
              title={t("settings.rows.configPath")}
              description={t("settings.help.configPath")}
            >
              <span className="max-w-[260px] truncate text-right text-[13px] text-muted-foreground">
                {settings.runtime.config_path || t("settings.values.notAvailable")}
              </span>
            </SettingsRow>
          </SettingsGroup>
        </section>
      )}
    </div>
  );
}

const SKILL_STATUS_FILTERS = ["", "proposed", "active", "deprecated", "rejected"] as const;
const SKILL_SOURCE_FILTERS = ["", "workspace", "builtin"] as const;

function emptySkillStats(): SkillLifecycleStats {
  return {
    skills_count: 0,
    workspace_skills_count: 0,
    skill_lifecycle_status_counts: {},
    skill_verification_status_counts: {},
    unverified_skill_count: 0,
    deprecated_skill_count: 0,
    rejected_skill_count: 0,
    always_workspace_skill_count: 0,
  };
}

function SkillsSettings() {
  const { t } = useTranslation();
  const { token, refreshToken } = useClient();
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [stats, setStats] = useState<SkillLifecycleStats>(emptySkillStats);
  const [selectedName, setSelectedName] = useState<string>("");
  const [selected, setSelected] = useState<SkillRecord | null>(null);
  const [sourceFilter, setSourceFilter] = useState<(typeof SKILL_SOURCE_FILTERS)[number]>("workspace");
  const [statusFilter, setStatusFilter] = useState<(typeof SKILL_STATUS_FILTERS)[number]>("");
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [acting, setActing] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        listSkills(freshToken, {
          source: sourceFilter || undefined,
          status: statusFilter || undefined,
          limit: 100,
        }),
      );
      setSkills(payload.skills);
      setStats(payload.stats);
      setError(null);
      setSelectedName((current) => {
        if (current && payload.skills.some((skill) => skill.name === current)) return current;
        return payload.skills[0]?.name ?? "";
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [refreshToken, sourceFilter, statusFilter, token]);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedName) {
      setSelected(null);
      return;
    }
    setDetailLoading(true);
    withTokenRefresh(token, refreshToken, (freshToken) => fetchSkill(freshToken, selectedName))
      .then((payload) => {
        if (!cancelled) {
          setSelected(payload.skill);
          setStats(payload.stats);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshToken, selectedName, token]);

  const runAction = async (
    action: "verify" | "activate" | "deprecate" | "reject" | "always",
    enabled?: boolean,
  ) => {
    if (!selected || acting) return;
    if (action === "activate" && !window.confirm(t("settings.skills.confirm.activate"))) return;
    if (action === "always" && enabled && !window.confirm(t("settings.skills.confirm.alwaysOn"))) return;
    setActing(action);
    try {
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        skillLifecycleAction(freshToken, selected.name, action, {
          enabled,
          reason,
        }),
      );
      setSelected(payload.skill);
      setStats(payload.stats);
      setNotice(payload.result.message);
      setReason("");
      setError(null);
      await loadSkills();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setActing(null);
    }
  };

  return (
    <div className="space-y-6">
      <p className="max-w-[42rem] text-[13px] leading-6 text-muted-foreground">
        {t("settings.skills.description")}
      </p>

      <div className="grid gap-3 sm:grid-cols-4">
        <SkillStat label={t("settings.skills.stats.total")} value={stats.skills_count} />
        <SkillStat label={t("settings.skills.stats.workspace")} value={stats.workspace_skills_count} />
        <SkillStat label={t("settings.skills.stats.unverified")} value={stats.unverified_skill_count} />
        <SkillStat label={t("settings.skills.stats.always")} value={stats.always_workspace_skill_count} />
      </div>

      {error ? (
        <div className="rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-[18px] border border-emerald-500/20 bg-emerald-500/8 px-4 py-3 text-[13px] text-emerald-700 dark:text-emerald-300">
          {notice}
        </div>
      ) : null}

      <section className="space-y-3">
        <SettingsSectionTitle>{t("settings.skills.filters.title")}</SettingsSectionTitle>
        <div className="flex flex-wrap gap-2">
          {SKILL_SOURCE_FILTERS.map((source) => (
            <Button
              key={source || "all"}
              type="button"
              size="sm"
              variant={sourceFilter === source ? "default" : "outline"}
              onClick={() => setSourceFilter(source)}
              className="rounded-full"
            >
              {t(source ? `settings.skills.source.${source}` : "settings.skills.filters.allSources")}
            </Button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {SKILL_STATUS_FILTERS.map((status) => (
            <Button
              key={status || "all"}
              type="button"
              size="sm"
              variant={statusFilter === status ? "default" : "outline"}
              onClick={() => setStatusFilter(status)}
              className="rounded-full"
            >
              {t(status ? `settings.skills.status.${status}` : "settings.skills.filters.allStatuses")}
            </Button>
          ))}
        </div>
      </section>

      <div className="grid min-h-[420px] gap-5 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.35fr)]">
        <SettingsGroup>
          {loading ? (
            <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("settings.skills.loading")}
            </div>
          ) : skills.length === 0 ? (
            <div className="px-4 py-8 text-sm text-muted-foreground">{t("settings.skills.empty")}</div>
          ) : (
            skills.map((skill) => {
              const active = skill.name === selectedName;
              return (
                <button
                  key={`${skill.source}:${skill.name}`}
                  type="button"
                  onClick={() => setSelectedName(skill.name)}
                  className={cn(
                    "flex min-h-[76px] w-full flex-col items-start gap-1 px-4 py-3 text-left transition-colors sm:px-5",
                    active ? "bg-muted/70" : "hover:bg-muted/35",
                  )}
                >
                  <span className="flex w-full items-center justify-between gap-3">
                    <span className="truncate text-[14px] font-semibold text-foreground">{skill.name}</span>
                    <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                      {t(`settings.skills.status.${skill.lifecycle_status || "active"}`)}
                    </span>
                  </span>
                  <span className="line-clamp-2 text-[12px] leading-5 text-muted-foreground">
                    {skill.description || skill.path}
                  </span>
                </button>
              );
            })
          )}
        </SettingsGroup>

        <SettingsGroup>
          {detailLoading ? (
            <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              {t("settings.skills.loading")}
            </div>
          ) : selected ? (
            <div className="divide-y divide-border/45">
              <div className="px-4 py-4 sm:px-5">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="mr-auto text-[17px] font-semibold tracking-[-0.02em] text-foreground">
                    {selected.name}
                  </h2>
                  <SkillBadge>{t(`settings.skills.source.${selected.source === "workspace" ? "workspace" : selected.source === "builtin" ? "builtin" : "domain"}`)}</SkillBadge>
                  <SkillBadge>{t(`settings.skills.status.${selected.lifecycle_status || "active"}`)}</SkillBadge>
                  <SkillBadge>{t(`settings.skills.verification.${selected.verification_status || "unknown"}`)}</SkillBadge>
                </div>
                <p className="mt-3 text-[13px] leading-6 text-muted-foreground">{selected.description}</p>
                <p className="mt-2 truncate text-[12px] text-muted-foreground">{selected.path}</p>
              </div>

              <div className="space-y-3 px-4 py-4 sm:px-5">
                <div className="grid gap-3 text-[12px] text-muted-foreground sm:grid-cols-2">
                  <SkillMeta label={t("settings.skills.fields.version")} value={selected.version || "1"} />
                  <SkillMeta label={t("settings.skills.fields.domain")} value={selected.domain_id || "core"} />
                  <SkillMeta label={t("settings.skills.fields.reviewed")} value={selected.reviewed_at || t("settings.values.notAvailable")} />
                  <SkillMeta label={t("settings.skills.fields.always")} value={selected.effective_always ? t("settings.skills.values.yes") : t("settings.skills.values.no")} />
                  <SkillMeta
                    label={t("settings.skills.fields.lastEvent")}
                    value={
                      typeof selected.last_event?.action === "string"
                        ? selected.last_event.action
                        : t("settings.values.notAvailable")
                    }
                  />
                </div>
                {selected.body_preview ? (
                  <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-[14px] bg-muted/55 p-3 text-[12px] leading-5 text-foreground/82">
                    {selected.body_preview}
                  </pre>
                ) : null}
                {selected.disabled_reason ? (
                  <p className="text-[12px] text-muted-foreground">{selected.disabled_reason}</p>
                ) : null}
              </div>

              <div className="space-y-3 px-4 py-4 sm:px-5">
                <label className="block space-y-1.5">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {t("settings.skills.reason")}
                  </span>
                  <textarea
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder={t("settings.skills.reasonPlaceholder")}
                    className="min-h-[74px] w-full resize-y rounded-[16px] border border-input bg-background px-3 py-2 text-[13px] text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" disabled={!selected.can_verify || !!acting} onClick={() => runAction("verify")} className="rounded-full">
                    {acting === "verify" ? t("settings.actions.saving") : t("settings.skills.actions.verify")}
                  </Button>
                  <Button size="sm" variant="outline" disabled={!selected.can_activate || !!acting} onClick={() => runAction("activate")} className="rounded-full">
                    {acting === "activate" ? t("settings.actions.saving") : t("settings.skills.actions.activate")}
                  </Button>
                  <Button size="sm" variant="outline" disabled={!selected.can_deprecate || !!acting} onClick={() => runAction("deprecate")} className="rounded-full">
                    {acting === "deprecate" ? t("settings.actions.saving") : t("settings.skills.actions.deprecate")}
                  </Button>
                  <Button size="sm" variant="outline" disabled={!selected.can_reject || !!acting} onClick={() => runAction("reject")} className="rounded-full">
                    {acting === "reject" ? t("settings.actions.saving") : t("settings.skills.actions.reject")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!selected.can_toggle_always || !!acting}
                    onClick={() => runAction("always", !selected.always)}
                    className="rounded-full"
                  >
                    {acting === "always"
                      ? t("settings.actions.saving")
                      : selected.always
                        ? t("settings.skills.actions.alwaysOff")
                        : t("settings.skills.actions.alwaysOn")}
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <div className="px-4 py-8 text-sm text-muted-foreground">{t("settings.skills.noSelection")}</div>
          )}
        </SettingsGroup>
      </div>
    </div>
  );
}

function SkillStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[18px] border border-border/45 bg-card/86 px-4 py-3 shadow-[0_12px_40px_rgba(15,23,42,0.055)]">
      <div className="text-[12px] font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 text-[22px] font-semibold text-foreground">{value}</div>
    </div>
  );
}

function SkillBadge({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-full bg-muted px-2.5 py-1 text-[11px] font-semibold text-muted-foreground">
      {children}
    </span>
  );
}

function SkillMeta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-medium text-foreground/75">{label}</div>
      <div className="mt-0.5 truncate">{value}</div>
    </div>
  );
}

function ProviderPicker({
  providers,
  value,
  emptyLabel,
  onChange,
}: {
  providers: Array<{ name: string; label: string }>;
  value: string;
  emptyLabel: string;
  onChange: (provider: string) => void;
}) {
  const selectedProvider = providers.find((provider) => provider.name === value) ?? null;
  const disabled = providers.length === 0;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          className={cn(
            "h-8 w-[210px] justify-between rounded-full border-input bg-background px-3 text-[13px] font-normal shadow-none",
            "hover:bg-accent/55 focus-visible:ring-2 focus-visible:ring-ring",
            disabled && "text-muted-foreground",
          )}
        >
          <span className="truncate">{selectedProvider?.label ?? emptyLabel}</span>
          <ChevronDown className="ml-2 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="max-h-[18rem] w-[240px] overflow-y-auto rounded-[18px] border-border/65 bg-popover p-1.5 text-popover-foreground shadow-[0_18px_55px_rgba(15,23,42,0.18)] dark:border-white/10 dark:shadow-[0_22px_55px_rgba(0,0,0,0.45)]"
      >
        {providers.map((provider) => {
          const selected = provider.name === value;
          return (
            <DropdownMenuItem
              key={provider.name}
              onSelect={() => onChange(provider.name)}
              className={cn(
                "flex cursor-default items-center justify-between gap-2 rounded-[12px] px-3 py-2 text-[13px]",
                "focus:bg-muted focus:text-foreground",
                selected && "bg-primary/10 text-primary focus:bg-primary/12 focus:text-primary",
              )}
            >
              <span className="truncate">{provider.label}</span>
              {selected ? <Check className="h-3.5 w-3.5 shrink-0" aria-hidden /> : null}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function WebSearchByokSettings({
  settings,
  form,
  keyVisible,
  keyEditing,
  saving,
  onChangeForm,
  onChangeProvider,
  onToggleKey,
  onToggleKeyEditing,
  onSave,
}: {
  settings: SettingsPayload;
  form: WebSearchSettingsUpdate;
  keyVisible: boolean;
  keyEditing: boolean;
  saving: boolean;
  onChangeForm: Dispatch<SetStateAction<WebSearchSettingsUpdate>>;
  onChangeProvider: (provider: string) => void;
  onToggleKey: () => void;
  onToggleKeyEditing: () => void;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  const selectedProvider =
    settings.web_search.providers.find((provider) => provider.name === form.provider) ??
    settings.web_search.providers[0];
  const hasExistingSecret =
    selectedProvider?.credential === "api_key" &&
    form.provider === settings.web_search.provider &&
    !!settings.web_search.api_key_hint;
  const showKeyInput = selectedProvider?.credential === "api_key" && (!hasExistingSecret || keyEditing);
  const apiKey = form.apiKey?.trim() ?? "";
  const baseUrl = form.baseUrl?.trim() ?? "";
  const dirty =
    form.provider !== settings.web_search.provider ||
    apiKey.length > 0 ||
    baseUrl !== (settings.web_search.base_url ?? "");
  const missingCredential =
    selectedProvider?.credential === "api_key"
      ? !apiKey && !hasExistingSecret
      : selectedProvider?.credential === "base_url"
        ? !baseUrl
        : false;

  return (
    <section className="space-y-4">
      <SettingsGroup>
        <SettingsRow
          title={t("settings.byok.webSearch.provider")}
          description={t("settings.byok.webSearch.providerHelp")}
        >
          <ProviderPicker
            providers={settings.web_search.providers}
            value={form.provider}
            emptyLabel={t("settings.byok.webSearch.selectProvider")}
            onChange={onChangeProvider}
          />
        </SettingsRow>

        {selectedProvider?.credential === "none" ? (
          <SettingsRow
            title={t("settings.byok.webSearch.credentials")}
            description={t("settings.byok.webSearch.noCredentialHelp")}
          >
            <span className="rounded-full bg-emerald-500/10 px-2.5 py-1 text-[12px] font-medium text-emerald-700 dark:text-emerald-300">
              {t("settings.byok.webSearch.noCredentialRequired")}
            </span>
          </SettingsRow>
        ) : null}

        {selectedProvider?.credential === "api_key" ? (
          <SettingsRow
            title={t("settings.byok.apiKey")}
            description={t("settings.byok.webSearch.apiKeyHelp")}
          >
            <div className="relative w-[280px] max-w-full">
              {showKeyInput ? (
                <>
                  <Input
                    type={keyVisible ? "text" : "password"}
                    value={form.apiKey ?? ""}
                    onChange={(event) =>
                      onChangeForm((prev) => ({ ...prev, apiKey: event.target.value }))
                    }
                    placeholder={
                      hasExistingSecret
                        ? t("settings.byok.apiKeyConfiguredPlaceholder")
                        : t("settings.byok.apiKeyPlaceholder")
                    }
                    className="h-9 rounded-full pr-11 text-[13px]"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={onToggleKey}
                    aria-label={
                      keyVisible ? t("settings.byok.hideApiKey") : t("settings.byok.showApiKey")
                    }
                    className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    {keyVisible ? (
                      <EyeOff className="h-3.5 w-3.5" aria-hidden />
                    ) : (
                      <Eye className="h-3.5 w-3.5" aria-hidden />
                    )}
                  </Button>
                </>
              ) : (
                <>
                  <div className="flex h-9 items-center rounded-full border border-input bg-background px-3 pr-11 text-[13px] text-muted-foreground">
                    {settings.web_search.api_key_hint ?? t("settings.byok.configuredKeyHint")}
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    onClick={onToggleKeyEditing}
                    aria-label={t("settings.actions.edit")}
                    className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </>
              )}
            </div>
          </SettingsRow>
        ) : null}

        {selectedProvider?.credential === "base_url" ? (
          <SettingsRow
            title={t("settings.byok.webSearch.baseUrl")}
            description={t("settings.byok.webSearch.baseUrlHelp")}
          >
            <Input
              value={form.baseUrl ?? ""}
              onChange={(event) =>
                onChangeForm((prev) => ({ ...prev, baseUrl: event.target.value }))
              }
              placeholder={t("settings.byok.webSearch.baseUrlPlaceholder")}
              className="h-9 w-[280px] rounded-full text-[13px]"
            />
          </SettingsRow>
        ) : null}

        <div className="flex min-h-[58px] items-center justify-between gap-4 px-4 py-3 sm:px-5">
          <div className="text-[13px] text-muted-foreground">
            {missingCredential
              ? t("settings.byok.webSearch.missingCredential")
              : t("settings.byok.webSearch.saveHint")}
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={onSave}
            disabled={!dirty || missingCredential || saving}
            className="rounded-full"
          >
            {saving ? t("settings.actions.saving") : t("settings.actions.save")}
          </Button>
        </div>
      </SettingsGroup>
    </section>
  );
}

function createEmptyMcpForm(): McpFormState {
  return {
    name: "",
    type: "stdio",
    command: "",
    args: "",
    env: "",
    url: "",
    headers: "",
    toolTimeout: "30",
    enabledTools: "*",
  };
}

function linesFromList(values: string[]): string {
  return values.join("\n");
}

function linesFromMapping(values: Record<string, string>): string {
  return Object.entries(values)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function formFromMcpServer(server: McpServerSettings): McpFormState {
  return {
    name: server.name,
    type: server.type ?? (server.command ? "stdio" : "streamableHttp"),
    command: server.command ?? "",
    args: linesFromList(server.args ?? []),
    env: linesFromMapping(server.env ?? {}),
    url: server.url ?? "",
    headers: linesFromMapping(server.headers ?? {}),
    toolTimeout: String(server.tool_timeout ?? 30),
    enabledTools: linesFromList(server.enabled_tools?.length ? server.enabled_tools : ["*"]),
  };
}

function listFromLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function mappingFromLines(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const rawLine of value.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const idx = line.indexOf("=");
    if (idx <= 0) continue;
    const key = line.slice(0, idx).trim();
    if (!key) continue;
    result[key] = line.slice(idx + 1);
  }
  return result;
}

function mcpUpdateFromForm(form: McpFormState): McpServerSettingsUpdate {
  const type = form.type;
  return {
    name: form.name.trim(),
    type,
    command: type === "stdio" ? form.command.trim() : "",
    args: type === "stdio" ? listFromLines(form.args) : [],
    env: type === "stdio" ? mappingFromLines(form.env) : {},
    url: type === "stdio" ? "" : form.url.trim(),
    headers: type === "stdio" ? {} : mappingFromLines(form.headers),
    tool_timeout: Number.parseInt(form.toolTimeout, 10) || 30,
    enabled_tools: listFromLines(form.enabledTools).length ? listFromLines(form.enabledTools) : ["*"],
  };
}

function McpSettings({
  settings,
  editingKey,
  forms,
  savingKey,
  deletingKey,
  homeAssistantForm,
  homeAssistantSaving,
  onChangeHomeAssistantForm,
  onSaveHomeAssistant,
  onAdd,
  onEdit,
  onCancel,
  onSave,
  onDelete,
  onChangeForm,
}: {
  settings: SettingsPayload;
  editingKey: string | null;
  forms: Record<string, McpFormState>;
  savingKey: string | null;
  deletingKey: string | null;
  homeAssistantForm: HomeAssistantMcpFormState;
  homeAssistantSaving: boolean;
  onChangeHomeAssistantForm: (value: Partial<HomeAssistantMcpFormState>) => void;
  onSaveHomeAssistant: () => void;
  onAdd: () => void;
  onEdit: (server: McpServerSettings) => void;
  onCancel: (key: string) => void;
  onSave: (key: string) => void;
  onDelete: (name: string) => void;
  onChangeForm: (key: string, value: Partial<McpFormState>) => void;
}) {
  const { t } = useTranslation();
  const servers = settings.mcp?.servers ?? [];
  const newForm = forms.__new__;
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <p className="max-w-[42rem] text-[13px] leading-6 text-muted-foreground">
          {t("settings.mcp.description")}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onAdd}
          disabled={editingKey === "__new__"}
          className="shrink-0 rounded-full"
        >
          <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
          {t("settings.mcp.add")}
        </Button>
      </div>

      {settings.requires_restart ? (
        <div className="rounded-[18px] border border-amber-500/20 bg-amber-500/8 px-4 py-3 text-[13px] text-amber-700 dark:text-amber-300">
          {t("settings.mcp.restartRequired")}
        </div>
      ) : null}

      <HomeAssistantMcpQuickConfig
        form={homeAssistantForm}
        saving={homeAssistantSaving}
        hasExistingToken={!!servers.find((server) => server.name === homeAssistantForm.name)?.headers?.Authorization}
        onChange={onChangeHomeAssistantForm}
        onSave={onSaveHomeAssistant}
      />

      {editingKey === "__new__" && newForm ? (
        <McpServerEditor
          form={newForm}
          title={t("settings.mcp.newServer")}
          saving={savingKey === "__new__"}
          onChange={(value) => onChangeForm("__new__", value)}
          onSave={() => onSave("__new__")}
          onCancel={() => onCancel("__new__")}
        />
      ) : null}

      <section className="space-y-3">
        <ByokSectionHeader title={t("settings.mcp.configuredServers")} count={servers.length} />
        <div className="overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.07)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.22)]">
          {servers.length > 0 ? (
            <div className="divide-y divide-border/45">
              {servers.map((server) => {
                const editing = editingKey === server.name;
                const form = forms[server.name] ?? formFromMcpServer(server);
                return (
                  <div key={server.name}>
                    <div className="flex min-h-[72px] items-center justify-between gap-4 px-4 py-3 sm:px-5">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-[15px] font-semibold text-foreground">
                            {server.name}
                          </span>
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[11.5px] font-medium text-muted-foreground">
                            {server.type ?? (server.command ? "stdio" : "streamableHttp")}
                          </span>
                        </div>
                        <div className="mt-1 truncate text-[12px] text-muted-foreground">
                          {server.command || server.url || t("settings.values.notAvailable")}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => onEdit(server)}
                          aria-label={t("settings.actions.edit")}
                          className="h-8 w-8 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                        >
                          <Pencil className="h-3.5 w-3.5" aria-hidden />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => onDelete(server.name)}
                          disabled={deletingKey === server.name}
                          aria-label={t("settings.mcp.delete")}
                          className="h-8 w-8 rounded-full text-muted-foreground hover:bg-destructive/8 hover:text-destructive"
                        >
                          {deletingKey === server.name ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                          ) : (
                            <Trash2 className="h-3.5 w-3.5" aria-hidden />
                          )}
                        </Button>
                      </div>
                    </div>
                    {editing ? (
                      <div className="border-t border-border/45 bg-muted/18 px-4 py-4 sm:px-5">
                        <McpServerEditor
                          form={form}
                          title={t("settings.mcp.editServer")}
                          saving={savingKey === server.name}
                          lockName
                          onChange={(value) => onChangeForm(server.name, value)}
                          onSave={() => onSave(server.name)}
                          onCancel={() => onCancel(server.name)}
                        />
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <ByokEmptyState>{t("settings.mcp.empty")}</ByokEmptyState>
          )}
        </div>
      </section>
    </div>
  );
}

function HomeAssistantMcpQuickConfig({
  form,
  saving,
  hasExistingToken,
  onChange,
  onSave,
}: {
  form: HomeAssistantMcpFormState;
  saving: boolean;
  hasExistingToken: boolean;
  onChange: (value: Partial<HomeAssistantMcpFormState>) => void;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  return (
    <section>
      <SettingsSectionTitle>{t("settings.mcp.quickConfig")}</SettingsSectionTitle>
      <SettingsGroup>
        <SettingsRow
          title={t("settings.mcp.homeAssistant.title")}
          description={t("settings.mcp.homeAssistant.description")}
        >
          <Server className="h-5 w-5 text-muted-foreground" aria-hidden />
        </SettingsRow>
        <SettingsRow title={t("settings.mcp.name")}>
          <Input
            value={form.name}
            onChange={(event) => onChange({ name: event.target.value })}
            placeholder="home_assistant"
            className="h-9 w-[280px] rounded-full text-[13px]"
          />
        </SettingsRow>
        <SettingsRow
          title={t("settings.mcp.homeAssistant.address")}
          description={t("settings.mcp.homeAssistant.addressHelp")}
        >
          <Input
            value={form.address}
            onChange={(event) => onChange({ address: event.target.value })}
            placeholder="http://localhost:8123"
            className="h-9 w-[280px] rounded-full text-[13px]"
          />
        </SettingsRow>
        <SettingsRow
          title={t("settings.mcp.homeAssistant.token")}
          description={
            hasExistingToken
              ? t("settings.mcp.homeAssistant.tokenHelpExisting")
              : t("settings.mcp.homeAssistant.tokenHelp")
          }
        >
          <Input
            type="password"
            value={form.token}
            onChange={(event) => onChange({ token: event.target.value })}
            placeholder={hasExistingToken ? "••••" : t("settings.mcp.homeAssistant.tokenPlaceholder")}
            className="h-9 w-[280px] rounded-full text-[13px]"
          />
        </SettingsRow>
        <div className="flex min-h-[58px] items-center justify-between gap-4 px-4 py-3 sm:px-5">
          <div className="max-w-[28rem] text-[13px] leading-5 text-muted-foreground">
            {t("settings.mcp.homeAssistant.saveHint")}
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onSave}
            disabled={saving}
            className="shrink-0 rounded-full"
          >
            {saving ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Check className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {saving ? t("settings.actions.saving") : t("settings.mcp.homeAssistant.save")}
          </Button>
        </div>
      </SettingsGroup>
    </section>
  );
}

function McpServerEditor({
  form,
  title,
  saving,
  lockName = false,
  onChange,
  onSave,
  onCancel,
}: {
  form: McpFormState;
  title: string;
  saving: boolean;
  lockName?: boolean;
  onChange: (value: Partial<McpFormState>) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const isStdio = form.type === "stdio";
  return (
    <SettingsGroup>
      <SettingsRow title={title} description={t("settings.mcp.editorHelp")}>
        <McpTransportPicker value={form.type} onChange={(type) => onChange({ type })} />
      </SettingsRow>
      <SettingsRow title={t("settings.mcp.name")}>
        <Input
          value={form.name}
          disabled={lockName}
          onChange={(event) => onChange({ name: event.target.value })}
          placeholder="github"
          className="h-9 w-[280px] rounded-full text-[13px]"
        />
      </SettingsRow>
      {isStdio ? (
        <>
          <SettingsRow title={t("settings.mcp.command")} description={t("settings.mcp.commandHelp")}>
            <Input
              value={form.command}
              onChange={(event) => onChange({ command: event.target.value })}
              placeholder="npx"
              className="h-9 w-[280px] rounded-full text-[13px]"
            />
          </SettingsRow>
          <TextareaRow
            title={t("settings.mcp.args")}
            description={t("settings.mcp.argsHelp")}
            value={form.args}
            placeholder={"-y\n@modelcontextprotocol/server-github"}
            onChange={(args) => onChange({ args })}
          />
          <TextareaRow
            title={t("settings.mcp.env")}
            description={t("settings.mcp.envHelp")}
            value={form.env}
            placeholder="GITHUB_PERSONAL_ACCESS_TOKEN=..."
            onChange={(env) => onChange({ env })}
          />
        </>
      ) : (
        <>
          <SettingsRow title={t("settings.mcp.url")}>
            <Input
              value={form.url}
              onChange={(event) => onChange({ url: event.target.value })}
              placeholder="https://example.com/mcp"
              className="h-9 w-[280px] rounded-full text-[13px]"
            />
          </SettingsRow>
          <TextareaRow
            title={t("settings.mcp.headers")}
            description={t("settings.mcp.headersHelp")}
            value={form.headers}
            placeholder="Authorization=Bearer ..."
            onChange={(headers) => onChange({ headers })}
          />
        </>
      )}
      <TextareaRow
        title={t("settings.mcp.enabledTools")}
        description={t("settings.mcp.enabledToolsHelp")}
        value={form.enabledTools}
        placeholder="*"
        onChange={(enabledTools) => onChange({ enabledTools })}
      />
      <SettingsRow title={t("settings.mcp.timeout")}>
        <Input
          type="number"
          min={1}
          value={form.toolTimeout}
          onChange={(event) => onChange({ toolTimeout: event.target.value })}
          className="h-9 w-[120px] rounded-full text-[13px]"
        />
      </SettingsRow>
      <div className="flex min-h-[58px] items-center justify-end gap-2 px-4 py-3 sm:px-5">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} className="rounded-full">
          {t("settings.actions.cancel")}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onSave} disabled={saving} className="rounded-full">
          {saving ? t("settings.actions.saving") : t("settings.actions.save")}
        </Button>
      </div>
    </SettingsGroup>
  );
}

function McpTransportPicker({
  value,
  onChange,
}: {
  value: McpTransportType;
  onChange: (value: McpTransportType) => void;
}) {
  const { t } = useTranslation();
  const options: McpTransportType[] = ["stdio", "sse", "streamableHttp"];
  return (
    <div className="inline-flex rounded-full bg-muted p-0.5 text-[12px] font-medium text-muted-foreground">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          className={cn(
            "rounded-full px-3 py-1 transition-colors",
            value === option && "bg-background text-foreground shadow-sm",
          )}
        >
          {t(`settings.mcp.transport.${option}`)}
        </button>
      ))}
    </div>
  );
}

function TextareaRow({
  title,
  description,
  value,
  placeholder,
  onChange,
}: {
  title: string;
  description?: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  return (
    <SettingsRow title={title} description={description}>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="min-h-[88px] w-[280px] max-w-full resize-y rounded-[16px] border border-input bg-background px-3 py-2 text-[13px] text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
      />
    </SettingsRow>
  );
}

function ByokSettings({
  settings,
  expandedProvider,
  providerForms,
  visibleProviderKeys,
  editingProviderKeys,
  providerSaving,
  webSearchForm,
  webSearchKeyVisible,
  webSearchKeyEditing,
  webSearchSaving,
  onToggleProvider,
  onToggleProviderKey,
  onToggleProviderKeyEditing,
  onChangeProviderForm,
  onSaveProvider,
  onChangeWebSearchForm,
  onChangeWebSearchProvider,
  onToggleWebSearchKey,
  onToggleWebSearchKeyEditing,
  onResetProviderDraft,
  onResetWebSearchDraft,
  onSaveWebSearch,
}: {
  settings: SettingsPayload;
  expandedProvider: string | null;
  providerForms: Record<string, { apiKey: string; apiBase: string }>;
  visibleProviderKeys: Record<string, boolean>;
  editingProviderKeys: Record<string, boolean>;
  providerSaving: string | null;
  webSearchForm: WebSearchSettingsUpdate;
  webSearchKeyVisible: boolean;
  webSearchKeyEditing: boolean;
  webSearchSaving: boolean;
  onToggleProvider: (provider: string) => void;
  onToggleProviderKey: (provider: string) => void;
  onToggleProviderKeyEditing: (provider: string) => void;
  onChangeProviderForm: (provider: string, value: Partial<{ apiKey: string; apiBase: string }>) => void;
  onSaveProvider: (provider: string) => void;
  onChangeWebSearchForm: Dispatch<SetStateAction<WebSearchSettingsUpdate>>;
  onChangeWebSearchProvider: (provider: string) => void;
  onToggleWebSearchKey: () => void;
  onToggleWebSearchKeyEditing: () => void;
  onResetProviderDraft: (provider: string) => void;
  onResetWebSearchDraft: () => void;
  onSaveWebSearch: () => void;
}) {
  const { t } = useTranslation();
  const [activePane, setActivePane] = useState<ByokPaneKey>("llm");
  const [showAllUnconfigured, setShowAllUnconfigured] = useState(false);
  const configuredProviders = settings.providers.filter((provider) => provider.configured);
  const unconfiguredProviders = settings.providers.filter((provider) => !provider.configured);
  const initialUnconfiguredCount = 6;
  const visibleUnconfiguredProviders = showAllUnconfigured
    ? unconfiguredProviders
    : unconfiguredProviders.slice(0, initialUnconfiguredCount);
  const hiddenUnconfiguredCount = Math.max(
    0,
    unconfiguredProviders.length - visibleUnconfiguredProviders.length,
  );
  const renderProviderRow = (provider: SettingsPayload["providers"][number]) => {
    const expanded = expandedProvider === provider.name;
    const form = providerForms[provider.name] ?? {
      apiKey: "",
      apiBase: provider.api_base ?? provider.default_api_base ?? "",
    };
    const saving = providerSaving === provider.name;
    const keyVisible = !!visibleProviderKeys[provider.name];
    const editingKey = !provider.configured || !!editingProviderKeys[provider.name];
    return (
      <div
        key={provider.name}
        className="divide-y divide-border/45"
      >
        <button
          type="button"
          onClick={() => onToggleProvider(provider.name)}
          className="flex min-h-[70px] w-full items-center justify-between gap-4 px-4 py-3 text-left transition-colors hover:bg-muted/35 sm:px-5"
        >
          <span className="flex min-w-0 items-center gap-3">
            <ProviderIcon provider={provider.name} />
            <span className="min-w-0">
              <span className="block truncate text-[15px] font-semibold leading-5 text-foreground">
                {provider.label}
              </span>
            </span>
          </span>
          <span
            className={cn(
              "rounded-full px-2.5 py-1 text-[12px] font-medium",
              provider.configured
                ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "bg-muted text-muted-foreground",
            )}
          >
            {provider.configured
              ? t("settings.byok.configured")
              : t("settings.byok.notConfigured")}
          </span>
        </button>

        {expanded ? (
          <div className="space-y-3 bg-muted/18 px-4 py-4 sm:px-5">
            <label className="block space-y-1.5">
              <span className="text-[12px] font-medium text-muted-foreground">
                {t("settings.byok.apiKey")}
              </span>
              <div className="relative">
                {editingKey ? (
                  <>
                    <Input
                      type={keyVisible ? "text" : "password"}
                      value={form.apiKey}
                      onChange={(event) =>
                        onChangeProviderForm(provider.name, { apiKey: event.target.value })
                      }
                      placeholder={
                        provider.configured
                          ? t("settings.byok.apiKeyConfiguredPlaceholder")
                          : t("settings.byok.apiKeyPlaceholder")
                      }
                      className="h-9 rounded-full pr-11 text-[13px]"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => onToggleProviderKey(provider.name)}
                      aria-label={
                        keyVisible
                          ? t("settings.byok.hideApiKey")
                          : t("settings.byok.showApiKey")
                      }
                      className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      {keyVisible ? (
                        <EyeOff className="h-3.5 w-3.5" aria-hidden />
                      ) : (
                        <Eye className="h-3.5 w-3.5" aria-hidden />
                      )}
                    </Button>
                  </>
                ) : (
                  <>
                    <div className="flex h-9 items-center rounded-full border border-input bg-background px-3 pr-11 text-[13px] text-muted-foreground">
                      {provider.api_key_hint ?? t("settings.byok.configuredKeyHint")}
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => onToggleProviderKeyEditing(provider.name)}
                      aria-label={t("settings.actions.edit")}
                      className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2 rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden />
                    </Button>
                  </>
                )}
              </div>
            </label>
            <label className="block space-y-1.5">
              <span className="text-[12px] font-medium text-muted-foreground">
                {t("settings.byok.apiBase")}
              </span>
              <Input
                value={form.apiBase}
                onChange={(event) =>
                  onChangeProviderForm(provider.name, { apiBase: event.target.value })
                }
                placeholder={provider.default_api_base ?? t("settings.byok.apiBasePlaceholder")}
                className="h-9 rounded-full text-[13px]"
              />
            </label>
            <div className="flex items-center justify-end">
              <Button
                size="sm"
                variant="outline"
                onClick={() => onSaveProvider(provider.name)}
                disabled={saving || (!provider.configured && !form.apiKey.trim())}
                className="rounded-full"
              >
                {saving ? t("settings.actions.saving") : t("settings.actions.save")}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    );
  };
  const panes: Array<{ key: ByokPaneKey; label: string }> = [
    { key: "llm", label: t("settings.byok.tabs.llm") },
    { key: "web-search", label: t("settings.byok.tabs.webSearch") },
  ];
  return (
    <div className="space-y-6">
      <p className="max-w-[42rem] text-[13px] leading-6 text-muted-foreground">
        {t("settings.byok.description")}
      </p>
      <div
        role="tablist"
        aria-label={t("settings.byok.tabs.ariaLabel")}
        className="grid rounded-[22px] border border-border/35 bg-muted/35 p-1 shadow-[inset_0_1px_2px_rgba(15,23,42,0.04)] backdrop-blur-xl sm:grid-cols-2"
      >
        {panes.map((pane) => {
          const selected = activePane === pane.key;
          return (
            <button
              key={pane.key}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => {
                if (pane.key === activePane) return;
                if (activePane === "llm" && expandedProvider) {
                  onResetProviderDraft(expandedProvider);
                }
                if (activePane === "web-search") {
                  onResetWebSearchDraft();
                }
                setActivePane(pane.key);
              }}
              className={cn(
                "h-10 rounded-[18px] text-[13px] font-semibold transition-all",
                selected
                  ? "bg-background text-foreground shadow-[0_8px_28px_rgba(15,23,42,0.10)]"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {pane.label}
            </button>
          );
        })}
      </div>
      {activePane === "llm" ? (
        <div className="space-y-8">
          <section className="space-y-3">
            <ByokSectionHeader
              title={t("settings.byok.configuredSection")}
              count={configuredProviders.length}
            />
            <div className="overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.07)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.22)]">
              {configuredProviders.length > 0 ? (
                <div className="divide-y divide-border/45">
                  {configuredProviders.map(renderProviderRow)}
                </div>
              ) : (
                <ByokEmptyState>{t("settings.byok.noConfiguredProviders")}</ByokEmptyState>
              )}
            </div>
          </section>

          <section className="space-y-3">
            <ByokSectionHeader
              title={t("settings.byok.notConfiguredSection")}
              count={unconfiguredProviders.length}
            />
            <div className="overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.07)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.22)]">
              <div className="divide-y divide-border/45">
                {visibleUnconfiguredProviders.map(renderProviderRow)}
              </div>
            </div>
            {hiddenUnconfiguredCount > 0 ? (
              <Button
                type="button"
                variant="ghost"
                onClick={() => setShowAllUnconfigured(true)}
                className="h-9 rounded-full px-3 text-[13px] text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              >
                {t("settings.byok.showMore", { count: hiddenUnconfiguredCount })}
              </Button>
            ) : showAllUnconfigured && unconfiguredProviders.length > initialUnconfiguredCount ? (
              <Button
                type="button"
                variant="ghost"
                onClick={() => setShowAllUnconfigured(false)}
                className="h-9 rounded-full px-3 text-[13px] text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              >
                {t("settings.byok.showLess")}
              </Button>
            ) : null}
          </section>
        </div>
      ) : (
        <WebSearchByokSettings
          settings={settings}
          form={webSearchForm}
          keyVisible={webSearchKeyVisible}
          keyEditing={webSearchKeyEditing}
          saving={webSearchSaving}
          onChangeForm={onChangeWebSearchForm}
          onChangeProvider={onChangeWebSearchProvider}
          onToggleKey={onToggleWebSearchKey}
          onToggleKeyEditing={onToggleWebSearchKeyEditing}
          onSave={onSaveWebSearch}
        />
      )}
    </div>
  );
}

function ByokSectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="flex items-center justify-between px-1">
      <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
        {title}
      </h2>
      <span className="rounded-full bg-muted px-2 py-0.5 text-[11.5px] font-medium text-muted-foreground">
        {count}
      </span>
    </div>
  );
}

function ByokEmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-[18px] border border-dashed border-border/65 bg-card/45 px-4 py-5 text-[13px] text-muted-foreground">
      {children}
    </div>
  );
}

const PROVIDER_ICONS: Record<string, LucideIcon> = {
  custom: Hexagon,
  openrouter: Sparkles,
  aihubmix: Triangle,
  anthropic: Brain,
  openai: Bot,
  deepseek: Waves,
  zhipu: Grid3X3,
  dashscope: Cloud,
  moonshot: Moon,
  minimax: Zap,
  minimax_anthropic: Brain,
  groq: Cpu,
  huggingface: Layers,
  gemini: Gem,
  mistral: Orbit,
  siliconflow: Layers,
  volcengine: Cloud,
  volcengine_coding_plan: Cloud,
  byteplus: Cloud,
  byteplus_coding_plan: Cloud,
  qianfan: Database,
  azure_openai: Cloud,
  bedrock: Database,
};

function ProviderIcon({ provider }: { provider: string }) {
  const Icon = PROVIDER_ICONS[provider] ?? Hexagon;
  return (
    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-muted text-foreground/82 shadow-[inset_0_0_0_1px_rgba(0,0,0,0.025)] dark:bg-muted/70">
      <Icon className="h-5 w-5" strokeWidth={2} aria-hidden />
    </span>
  );
}

function SettingsSectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="mb-2 px-1 text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
      {children}
    </h2>
  );
}

function SettingsGroup({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]">
      <div className="divide-y divide-border/45">{children}</div>
    </div>
  );
}

function SettingsRow({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex min-h-[62px] flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="min-w-0">
        <div className="text-[14px] font-medium leading-5 text-foreground">{title}</div>
        {description ? (
          <div className="mt-0.5 max-w-[28rem] text-[12px] leading-5 text-muted-foreground">
            {description}
          </div>
        ) : null}
      </div>
      {children ? <div className="shrink-0 sm:ml-6">{children}</div> : null}
    </div>
  );
}

function SettingsFooter({
  dirty,
  saving,
  saved,
  onSave,
}: {
  dirty: boolean;
  saving: boolean;
  saved: boolean;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-[58px] items-center justify-between gap-4 px-4 py-3 sm:px-5">
      <div className="text-[13px] text-muted-foreground">
        {saved ? t("settings.status.savedRestart") : t("settings.status.unsaved")}
      </div>
      <Button size="sm" variant="outline" onClick={onSave} disabled={!dirty || saving} className="rounded-full">
        {saving ? t("settings.actions.saving") : t("settings.actions.save")}
      </Button>
    </div>
  );
}

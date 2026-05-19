import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button as IslandButton, Card as IslandCard, Input as IslandInput, Typewriter } from "animal-island-ui";
import { useTranslation } from "react-i18next";
import { DeleteConfirm } from "@/components/DeleteConfirm";
import { ReviewsView } from "@/components/reviews/ReviewsView";
import { Sidebar } from "@/components/Sidebar";
import { SettingsView } from "@/components/settings/SettingsView";
import { ThreadShell } from "@/components/thread/ThreadShell";
import { Sheet, SheetContent } from "@/components/ui/sheet";

import { useSessions } from "@/hooks/useSessions";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import {
  clearSavedSecret,
  deriveWsUrl,
  fetchBootstrap,
  loadSavedSecret,
  saveSecret,
} from "@/lib/bootstrap";
import { OpenHomeClient } from "@/lib/OpenHome-client";
import { ClientProvider, useClient } from "@/providers/ClientProvider";
import type { ChatSummary } from "@/lib/types";

type BootState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "auth"; failed?: boolean }
  | {
      status: "ready";
      client: OpenHomeClient;
      token: string;
      modelName: string | null;
      refreshToken: () => Promise<string | null>;
    };

const SIDEBAR_STORAGE_KEY = "OpenHome-webui.sidebar";
const RESTART_STARTED_KEY = "OpenHome-webui.restartStartedAt";
const SIDEBAR_WIDTH = 272;
type ShellView = "chat" | "settings" | "reviews";

function pendingSessionFromKey(key: string): ChatSummary | null {
  const separator = key.indexOf(":");
  if (separator <= 0 || separator === key.length - 1) return null;
  const channel = key.slice(0, separator);
  const chatId = key.slice(separator + 1);
  return {
    key,
    channel,
    chatId,
    createdAt: null,
    updatedAt: null,
    title: "",
    preview: "",
  };
}

function AuthForm({
  failed,
  onSecret,
}: {
  failed: boolean;
  onSecret: (secret: string) => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const secret = value.trim();
    if (!secret) return;
    setSubmitting(true);
    onSecret(secret);
  };

  return (
    <div className="island-shell-bg flex h-full w-full items-center justify-center px-6">
      <form
        onSubmit={handleSubmit}
        className="island-auth-card flex w-full max-w-sm flex-col gap-4"
      >
        <div className="flex flex-col items-center gap-1 text-center">
          <div className="island-brand-orb mb-2">
            <picture>
              <img src="/brand/OpenHome_mark_v2.svg" alt="OpenHome" draggable={false} />
            </picture>
          </div>
          <p className="text-lg font-black text-[#725d42]">{t("app.auth.title")}</p>
          <p className="text-sm text-muted-foreground">{t("app.auth.hint")}</p>
        </div>
        {failed && (
          <p className="text-center text-sm text-destructive">
            {t("app.auth.invalid")}
          </p>
        )}
        <IslandInput
          name="openhome-secret"
          type="password"
          placeholder={t("app.auth.placeholder")}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={submitting}
          autoFocus
          size="large"
          allowClear
        />
        <IslandButton
          htmlType="submit"
          type="primary"
          size="large"
          block
          loading={submitting}
          disabled={!value.trim() || submitting}
        >
          {t("app.auth.submit")}
        </IslandButton>
      </form>
    </div>
  );
}

function readSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const raw = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (raw === null) return true;
    return raw === "1";
  } catch {
    return true;
  }
}

export default function App() {
  const { t } = useTranslation();
  const [state, setState] = useState<BootState>({ status: "loading" });

  const bootstrapWithSecret = useCallback(
    (secret: string) => {
      let cancelled = false;
      (async () => {
        setState({ status: "loading" });
        try {
          const boot = await fetchBootstrap("", secret);
          if (cancelled) return;
          if (secret) saveSecret(secret);
          const url = deriveWsUrl(boot.ws_path, boot.token);
          let client: OpenHomeClient;
          const refreshToken = async (): Promise<string | null> => {
            try {
              const refreshed = await fetchBootstrap("", secret);
              const refreshedUrl = deriveWsUrl(refreshed.ws_path, refreshed.token);
              client.updateUrl(refreshedUrl);
              setState((current) =>
                current.status === "ready"
                  ? {
                      ...current,
                      token: refreshed.token,
                      modelName: refreshed.model_name ?? current.modelName,
                    }
                  : current,
              );
              return refreshed.token;
            } catch {
              return null;
            }
          };
          const refreshWsUrl = async (): Promise<string | null> => {
            try {
              const refreshed = await fetchBootstrap("", secret);
              const refreshedUrl = deriveWsUrl(refreshed.ws_path, refreshed.token);
              client.updateUrl(refreshedUrl);
              setState((current) =>
                current.status === "ready"
                  ? {
                      ...current,
                      token: refreshed.token,
                      modelName: refreshed.model_name ?? current.modelName,
                    }
                  : current,
              );
              return refreshedUrl;
            } catch {
              return null;
            }
          };
          client = new OpenHomeClient({
            url,
            onReauth: refreshWsUrl,
          });
          client.connect();
          setState({
            status: "ready",
            client,
            token: boot.token,
            modelName: boot.model_name ?? null,
            refreshToken,
          });
        } catch (e) {
          if (cancelled) return;
          const msg = (e as Error).message;
          if (msg.includes("HTTP 401") || msg.includes("HTTP 403")) {
            setState({ status: "auth", failed: true });
          } else {
            setState({ status: "error", message: msg });
          }
        }
      })();
      return () => {
        cancelled = true;
      };
    },
    [],
  );

  useEffect(() => {
    const saved = loadSavedSecret();
    return bootstrapWithSecret(saved);
  }, [bootstrapWithSecret]);

  if (state.status === "loading") {
    return (
      <div className="island-shell-bg flex h-full w-full items-center justify-center px-6">
        <IslandCard className="island-status-card animate-in fade-in-0 duration-300">
          <div className="island-loader-leaf" aria-hidden />
          <Typewriter speed={36}>{t("app.loading.connecting")}</Typewriter>
        </IslandCard>
      </div>
    );
  }
  if (state.status === "auth") {
    return (
      <AuthForm
        failed={!!state.failed}
        onSecret={(s) => bootstrapWithSecret(s)}
      />
    );
  }
  if (state.status === "error") {
    return (
      <div className="island-shell-bg flex h-full w-full items-center justify-center px-4 text-center">
        <IslandCard className="island-status-card flex max-w-md flex-col items-center gap-3">
          <p className="text-lg font-black text-[#725d42]">{t("app.error.title")}</p>
          <p className="text-sm text-muted-foreground">{state.message}</p>
          <p className="text-xs text-muted-foreground">
            {t("app.error.gatewayHint")}
          </p>
        </IslandCard>
      </div>
    );
  }

  const handleModelNameChange = (modelName: string | null) => {
    setState((current) =>
      current.status === "ready" ? { ...current, modelName } : current,
    );
  };

  const handleLogout = () => {
    if (state.status === "ready") {
      state.client.close();
    }
    clearSavedSecret();
    setState({ status: "auth" });
  };

  return (
    <ClientProvider
      client={state.client}
      token={state.token}
      modelName={state.modelName}
      refreshToken={state.refreshToken}
    >
      <Shell onModelNameChange={handleModelNameChange} onLogout={handleLogout} />
    </ClientProvider>
  );
}

function Shell({ onModelNameChange, onLogout }: { onModelNameChange: (modelName: string | null) => void; onLogout: () => void }) {
  const { t, i18n } = useTranslation();
  const { client } = useClient();
  const { theme, toggle } = useTheme();
  const { sessions, loading, refresh, createChat, deleteChat } = useSessions();
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [view, setView] = useState<ShellView>("chat");
  const [desktopSidebarOpen, setDesktopSidebarOpen] =
    useState<boolean>(readSidebarOpen);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{
    key: string;
    label: string;
  } | null>(null);
  const restartSawDisconnectRef = useRef(false);
  const [restartToast, setRestartToast] = useState<string | null>(null);
  const [isRestarting, setIsRestarting] = useState(false);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        SIDEBAR_STORAGE_KEY,
        desktopSidebarOpen ? "1" : "0",
      );
    } catch {
      // ignore storage errors (private mode, etc.)
    }
  }, [desktopSidebarOpen]);



  const activeSession = useMemo<ChatSummary | null>(() => {
    if (!activeKey) return null;
    const session = sessions.find((s) => s.key === activeKey);
    if (session) return session;
    return loading ? pendingSessionFromKey(activeKey) : null;
  }, [sessions, activeKey, loading]);

  const closeDesktopSidebar = useCallback(() => {
    setDesktopSidebarOpen(false);
  }, []);

  const closeMobileSidebar = useCallback(() => {
    setMobileSidebarOpen(false);
  }, []);

  const toggleSidebar = useCallback(() => {
    const isDesktop =
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 1024px)").matches;
    if (isDesktop) {
      setDesktopSidebarOpen((v) => !v);
    } else {
      setMobileSidebarOpen((v) => !v);
    }
  }, []);

  const onCreateChat = useCallback(async () => {
    try {
      const chatId = await createChat();
      setActiveKey(`websocket:${chatId}`);
      setView("chat");
      setMobileSidebarOpen(false);
      return chatId;
    } catch (e) {
      console.error("Failed to create chat", e);
      return null;
    }
  }, [createChat]);

  const onNewChat = useCallback(() => {
    setActiveKey(null);
    setView("chat");
    setMobileSidebarOpen(false);
  }, []);

  const onSelectChat = useCallback(
    (key: string) => {
      setActiveKey(key);
      setView("chat");
      setMobileSidebarOpen(false);
    },
    [],
  );

  const onOpenSettings = useCallback(() => {
    setView("settings");
    setMobileSidebarOpen(false);
  }, []);

  const onOpenReviews = useCallback(() => {
    setView("reviews");
    setMobileSidebarOpen(false);
  }, []);

  const onBackToChat = useCallback(() => {
    setView("chat");
    setMobileSidebarOpen(false);
    setActiveKey((current) => {
      if (!current) return null;
      if (sessions.some((session) => session.key === current)) return current;
      return sessions[0]?.key ?? null;
    });
  }, [sessions]);

  const onRestart = useCallback(() => {
    const chatId = activeSession?.chatId ?? client.defaultChatId;
    if (!chatId) return;
    restartSawDisconnectRef.current = false;
    setIsRestarting(true);
    try {
      window.localStorage.setItem(RESTART_STARTED_KEY, String(Date.now()));
    } catch {
      // ignore storage errors
    }
    client.sendMessage(chatId, "/restart");
  }, [activeSession?.chatId, client]);

  useEffect(() => {
    return client.onRuntimeModelUpdate((modelName) => {
      onModelNameChange(modelName);
    });
  }, [client, onModelNameChange]);

  useEffect(() => {
    return client.onStatus((status) => {
      let startedAt = 0;
      try {
        startedAt = Number(window.localStorage.getItem(RESTART_STARTED_KEY) ?? "0");
      } catch {
        startedAt = 0;
      }
      if (!startedAt) return;
      if (status !== "open") {
        restartSawDisconnectRef.current = true;
        return;
      }
      const elapsedMs = Date.now() - startedAt;
      if (!restartSawDisconnectRef.current && elapsedMs < 1500) return;
      try {
        window.localStorage.removeItem(RESTART_STARTED_KEY);
      } catch {
        // ignore storage errors
      }
      setIsRestarting(false);
      setRestartToast(t("app.restart.completed", { seconds: (elapsedMs / 1000).toFixed(1) }));
      window.setTimeout(() => setRestartToast(null), 3_500);
    });
  }, [client, t]);

  const onTurnEnd = useCallback(() => {
    void refresh();
  }, [refresh]);

  const onConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    const key = pendingDelete.key;
    const deletingActive = activeKey === key;
    const currentIndex = sessions.findIndex((s) => s.key === key);
    const fallbackKey = deletingActive
      ? (sessions[currentIndex + 1]?.key ?? sessions[currentIndex - 1]?.key ?? null)
      : activeKey;
    setPendingDelete(null);
    if (deletingActive) setActiveKey(fallbackKey);
    try {
      await deleteChat(key);
    } catch (e) {
      if (deletingActive) setActiveKey(key);
      console.error("Failed to delete session", e);
    }
  }, [pendingDelete, deleteChat, activeKey, sessions]);

  const headerTitle = activeSession
    ? activeSession.title ||
      activeSession.preview ||
      t("chat.pendingTitle", { defaultValue: t("chat.newChat") })
    : t("app.brand");

  useEffect(() => {
    if (view === "settings") {
      document.title = t("app.documentTitle.chat", {
        title: t("settings.sidebar.title"),
      });
      return;
    }
    if (view === "reviews") {
      document.title = t("app.documentTitle.chat", {
        title: t("reviews.title"),
      });
      return;
    }
    document.title = activeSession
      ? t("app.documentTitle.chat", { title: headerTitle })
      : t("app.documentTitle.base");
  }, [activeSession, headerTitle, i18n.resolvedLanguage, t, view]);

  const sidebarProps = {
    sessions,
    activeKey,
    loading,
    onNewChat,
    onSelect: onSelectChat,
    onRequestDelete: (key: string, label: string) =>
      setPendingDelete({ key, label }),
    onOpenSettings,
    onOpenReviews,
  };
  const showMainSidebar = view !== "settings";

  return (
    <div className="island-app-shell relative flex h-full w-full overflow-hidden">
      {/* Desktop sidebar: in normal flow, so the thread area width stays honest. */}
      {showMainSidebar ? (
        <aside
          className={cn(
            "relative z-20 hidden shrink-0 overflow-hidden lg:block",
            "transition-[width] duration-300 ease-out",
          )}
          style={{ width: desktopSidebarOpen ? SIDEBAR_WIDTH : 0 }}
        >
          <div
            className={cn(
              "island-sidebar absolute inset-y-0 left-0 right-2.5 h-full overflow-hidden bg-sidebar shadow-inner-right",
              "transition-transform duration-300 ease-out",
              desktopSidebarOpen ? "translate-x-0" : "-translate-x-full",
            )}
          >
            <Sidebar {...sidebarProps} onCollapse={closeDesktopSidebar} />
          </div>
        </aside>
      ) : null}

      {showMainSidebar ? (
        <Sheet
          open={mobileSidebarOpen}
          onOpenChange={(open) => setMobileSidebarOpen(open)}
        >
          <SheetContent
            side="left"
            showCloseButton={false}
            className="p-0 lg:hidden"
            style={{ width: SIDEBAR_WIDTH, maxWidth: SIDEBAR_WIDTH }}
          >
            <Sidebar {...sidebarProps} onCollapse={closeMobileSidebar} />
          </SheetContent>
        </Sheet>
      ) : null}

      <main className="island-main-panel relative flex h-full min-w-0 flex-1 flex-col">
        <div
          className={cn(
            "absolute inset-0 flex flex-col",
            view !== "chat" && "invisible pointer-events-none",
          )}
        >
          <ThreadShell
            session={activeSession}
            title={headerTitle}
            onToggleSidebar={toggleSidebar}
            onNewChat={onNewChat}
            onCreateChat={onCreateChat}
            onTurnEnd={onTurnEnd}
            theme={theme}
            onToggleTheme={toggle}
            hideSidebarToggleOnDesktop={desktopSidebarOpen}
          />
        </div>
        {view === "settings" && (
          <div className="absolute inset-0 flex flex-col">
            <SettingsView
              theme={theme}
              onToggleTheme={toggle}
              onBackToChat={onBackToChat}
              onModelNameChange={onModelNameChange}
              onLogout={onLogout}
              onRestart={onRestart}
              isRestarting={isRestarting}
            />
          </div>
        )}
        {view === "reviews" && (
          <div className="absolute inset-0 flex flex-col">
            <ReviewsView onBackToChat={onBackToChat} />
          </div>
        )}
      </main>

      <DeleteConfirm
        open={!!pendingDelete}
        title={pendingDelete?.label ?? ""}
        onCancel={() => setPendingDelete(null)}
        onConfirm={onConfirmDelete}
      />
      {restartToast ? (
        <div
          role="status"
          className="fixed left-1/2 top-4 z-50 -translate-x-1/2 rounded-full border border-[#c4b89e]/70 bg-[#f7f3df] px-4 py-2 text-sm font-black text-[#725d42] shadow-lg"
        >
          {restartToast}
        </div>
      ) : null}
    </div>
  );
}

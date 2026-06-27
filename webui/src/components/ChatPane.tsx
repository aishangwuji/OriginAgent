import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Composer } from "@/components/Composer";
import { MessageList } from "@/components/MessageList";
import { useClient } from "@/providers/ClientProvider";
import { useOriginAgentStream } from "@/hooks/useOriginAgentStream";
import { VoiceOutputPlayer } from "@/components/voice/VoiceOutputPlayer";
import { useSessionHistory } from "@/hooks/useSessions";
import type { ChatSummary } from "@/lib/types";

interface ChatPaneProps {
  session: ChatSummary | null;
  /** Provision a new chat and mark it active. Returns the new chat_id or null. */
  onNewChat: () => Promise<string | null>;
}

/**
 * The chat surface: persisted history on top, live stream below, composer
 * pinned at the bottom. When no session is active we render a centered
 * welcome card with a fully-functional composer — typing a first message
 * quietly provisions a new chat and routes the message through.
 */
export function ChatPane({ session, onNewChat }: ChatPaneProps) {
  const chatId = session?.chatId ?? null;
  const historyKey = session?.key ?? null;
  const { messages: historical, loading, hasPendingToolCalls } = useSessionHistory(historyKey);
  const { client } = useClient();
  const [booting, setBooting] = useState(false);
  const pendingFirstRef = useRef<string | null>(null);
  const [voiceAudioUrl, setVoiceAudioUrl] = useState<string | null>(null);

  const initial = useMemo(() => historical, [historical]);
  const { messages, isStreaming, send, setMessages } = useOriginAgentStream(
    chatId,
    initial,
    hasPendingToolCalls,
    undefined,
    (audioUrl: string) => setVoiceAudioUrl(audioUrl),
  );

  useEffect(() => {
    if (!loading && chatId) setMessages(historical);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, chatId, historical]);

  // Once a session becomes active, flush any first-message stashed from the
  // welcome composer so the user's keystroke "just sends".
  useEffect(() => {
    if (!chatId) return;
    const pending = pendingFirstRef.current;
    if (!pending) return;
    pendingFirstRef.current = null;
    client.sendMessage(chatId, pending, undefined, { lang: localStorage.getItem("OriginAgent.locale") || undefined });
    setMessages((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: pending,
        createdAt: Date.now(),
      },
    ]);
    setBooting(false);
  }, [chatId, client, setMessages]);

  const handleVoiceSend = useCallback(
    (audioDataUrl: string) => {
      if (!chatId) return;
      client.sendVoiceMessage(chatId, audioDataUrl);
    },
    [chatId, client],
  );

  const handleWelcomeSend = useCallback(
    async (content: string) => {
      if (booting) return;
      setBooting(true);
      pendingFirstRef.current = content;
      const newId = await onNewChat();
      if (!newId) {
        // Creation failed — release the lock so the user can retry.
        pendingFirstRef.current = null;
        setBooting(false);
      }
    },
    [booting, onNewChat],
  );

  if (!session) {
    return (
      <section className="flex min-h-0 flex-1 flex-col">
        <div className="flex flex-1 flex-col items-center justify-center gap-8 px-4 pb-6">
          <div className="flex flex-col items-center gap-4 animate-in fade-in-0 slide-in-from-bottom-2 duration-500">
            <h1 className="text-xl font-medium tracking-tight text-foreground/90">
              What can I do for you?
            </h1>
            <p className="max-w-md text-center text-sm text-muted-foreground">
              Your conversations are persisted locally under the OriginAgent
              workspace. Start typing and I'll open a new chat.
            </p>
          </div>
          <div className="w-full animate-in fade-in-0 slide-in-from-bottom-2 duration-500">
            <Composer
              compact
              disabled={booting}
              onSend={handleWelcomeSend}
              onVoiceSend={handleVoiceSend}
              placeholder={
                booting ? "Opening a new chat…" : "Ask anything..."
              }
            />
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="relative flex min-h-0 flex-1 flex-col">
      <MessageList messages={messages} isStreaming={isStreaming} />
      {voiceAudioUrl && (
        <div className="flex justify-center px-4 pb-1">
          <VoiceOutputPlayer
            audioUrl={voiceAudioUrl}
            onEnded={() => setVoiceAudioUrl(null)}
          />
        </div>
      )}
      <Composer
        onSend={send}
        onVoiceSend={handleVoiceSend}
        disabled={!chatId}
        placeholder="Type your message…"
      />
    </section>
  );
}

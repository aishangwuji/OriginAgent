import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { listSignals, updateSignal, fetchEvolutionStatus } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import type { OpportunitySignal, EvolutionStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { SignalCard } from "./SignalCard";

interface SignalsViewProps {
  onBackToChat: () => void;
}

type SignalStatusFilter = "open" | "converted" | "suppressed" | "";
type SignalKindFilter = "workflow_candidate" | "skill_candidate" | "";

export function SignalsView({ onBackToChat }: SignalsViewProps) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [signals, setSignals] = useState<OpportunitySignal[]>([]);
  const [evolutionStatus, setEvolutionStatus] = useState<EvolutionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<SignalStatusFilter>("open");
  const [kindFilter, setKindFilter] = useState<SignalKindFilter>("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [signalPayload, statusPayload] = await Promise.all([
        listSignals(token, {
          status: statusFilter || undefined,
          kind: kindFilter || undefined,
          limit: 100,
        }),
        fetchEvolutionStatus(token),
      ]);
      setSignals(signalPayload.signals);
      setEvolutionStatus(statusPayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [token, statusFilter, kindFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSuppress = async (signalId: string) => {
    setActioning(signalId);
    try {
      await updateSignal(token, signalId, "suppress", { reason: "Manual suppression from WebUI" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActioning(null);
    }
  };

  const handleResume = async (signalId: string) => {
    setActioning(signalId);
    try {
      await updateSignal(token, signalId, "resume");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActioning(null);
    }
  };

  const statusSummary = evolutionStatus
    ? `${evolutionStatus.opportunity_signals_count} open \u00B7 ${evolutionStatus.converted_signals_count} converted \u00B7 ${evolutionStatus.suppressed_signals_count} suppressed`
    : "";

  return (
    <div className="flex h-full flex-col">
      <header className="flex min-h-[64px] items-center justify-between gap-3 border-b border-border/60 px-4 py-3 sm:px-5">
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={onBackToChat}
            aria-label={t("signals.backToChat", "Back to chat")}
          >
            <ChevronRight className="h-4 w-4 rotate-180" aria-hidden />
          </Button>
          <div>
            <h1 className="text-lg font-black leading-tight text-foreground">
              {t("signals.title", "Opportunity Signals")}
            </h1>
            {statusSummary && (
              <p className="mt-0.5 text-xs text-muted-foreground">{statusSummary}</p>
            )}
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void load()}
          disabled={loading}
          className="rounded-full"
        >
          <RefreshCw className={cn("mr-2 h-3.5 w-3.5", loading && "animate-spin")} />
          {t("signals.refresh", "Refresh")}
        </Button>
      </header>

      <div className="space-y-3 border-b border-border/60 p-3">
        <div className="flex flex-wrap gap-2">
          {([["", "All"], ["open", "Open"], ["converted", "Converted"], ["suppressed", "Suppressed"]] as const).map(
            ([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setStatusFilter(value)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-black transition",
                  statusFilter === value
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border/70 bg-background/60 text-muted-foreground hover:border-primary/70 hover:text-foreground",
                )}
              >
                {label}
              </button>
            ),
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {([["", "All"], ["workflow_candidate", "Workflow"], ["skill_candidate", "Skill"]] as const).map(
            ([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setKindFilter(value)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-black transition",
                  kindFilter === value
                    ? "border-accent bg-accent text-accent-foreground"
                    : "border-border/70 bg-background/60 text-muted-foreground hover:border-accent/70 hover:text-foreground",
                )}
              >
                {label}
              </button>
            ),
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : signals.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border/70 bg-background/45 p-8 text-center text-sm text-muted-foreground">
            {t("signals.empty", "No opportunity signals match the current filters.")}
          </div>
        ) : (
          <div className="mx-auto max-w-4xl space-y-3">
            {signals.map((signal) => (
              <SignalCard
                key={signal.opportunity_id}
                signal={signal}
                onSuppress={handleSuppress}
                onResume={handleResume}
                disabled={actioning === signal.opportunity_id}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

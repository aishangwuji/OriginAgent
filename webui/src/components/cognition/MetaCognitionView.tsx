import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Brain,
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { fetchMetaCognitionSummary, userFriendlyError } from "@/lib/api";
import { useClient } from "@/providers/ClientProvider";
import type { MetaCognitionSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TriggerTimeline } from "./TriggerTimeline";
import { ReflectionCard } from "./ReflectionCard";
import { PatternTable } from "./PatternTable";
import { BridgeStats } from "./BridgeStats";

interface MetaCognitionViewProps {
  onBackToChat: () => void;
}

export function MetaCognitionView({ onBackToChat }: MetaCognitionViewProps) {
  const { t } = useTranslation();
  const { token } = useClient();
  const [summary, setSummary] = useState<MetaCognitionSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedSection, setExpandedSection] = useState<string>("overview");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchMetaCognitionSummary(token);
      setSummary(data);
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  function toggleSection(key: string) {
    setExpandedSection((prev) => (prev === key ? "" : key));
  }

  if (loading && !summary) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error && !summary) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-destructive">{error}</p>
      </div>
    );
  }

  const s = summary ?? {
    enabled: false,
    contract_version: "",
    trigger_collection_enabled: false,
    structured_reflection_enabled: false,
    pattern_consolidation_enabled: false,
    evolution_bridge_enabled: false,
    runtime_status: { accepted_total: 0, suppressed_total: 0 } as const,
    uncertainty_stats: { avg: 0, max: 0, high_count: 0, threshold: 0.5 } as const,
    decision_counts: {} as Record<string, number>,
    suppression_reason_counts: {} as Record<string, number>,
    recent_triggers: [],
    recent_decisions: [],
    recent_journals: [],
    recent_reflections: [],
    recent_confidence_traces: [],
    recent_patterns: [],
    recent_evolution_seeds: [],
    bridge_decision_counts: {} as Record<string, number>,
    artifact_status: {} as Record<string, number | undefined>,
    working_memory_bridge: { enabled: false, last_status: "disabled", decision_counts: {} },
    memory_candidate_bridge: { enabled: false, last_status: "disabled", decision_counts: {} },
    pattern_counts: {} as Record<string, number>,
    seed_counts: {} as Record<string, number>,
    last_signal_upserts: [],
    fast_path_decision_counts: {} as Record<string, number>,
  } as MetaCognitionSummary;

  const totalDecision = Object.values(s.decision_counts).reduce((a, b) => a + b, 0);
  const suppressionReasons = Object.entries(s.suppression_reason_counts);

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
            aria-label={t("cognition.backToChat", "Back to chat")}
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
          </Button>
          <div>
            <h1 className="text-lg font-black leading-tight text-foreground">
              {t("cognition.title", "Meta-Cognition")}
            </h1>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {s.enabled
                ? t("cognition.active", "Active")
                : t("cognition.disabled", "Disabled")}
              {" \u00B7 "}
              {s.runtime_status.accepted_total} accepted \u00B7 {s.runtime_status.suppressed_total} suppressed
            </p>
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
          {t("cognition.refresh", "Refresh")}
        </Button>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="mx-auto max-w-4xl space-y-6 p-4 sm:p-6">
          {/* Stats Cards Row */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              icon={<Activity className="h-4 w-4" />}
              label={t("cognition.acceptedTriggers", "Accepted")}
              value={s.runtime_status.accepted_total}
              color="text-emerald-500"
            />
            <StatCard
              icon={<AlertTriangle className="h-4 w-4" />}
              label={t("cognition.suppressedTriggers", "Suppressed")}
              value={s.runtime_status.suppressed_total}
              color="text-amber-500"
            />
            <StatCard
              icon={<Brain className="h-4 w-4" />}
              label={t("cognition.reflections", "Reflections")}
              value={(s.artifact_status as Record<string, number>)?.reflections_written ?? 0}
              color="text-blue-500"
            />
            <StatCard
              icon={<Activity className="h-4 w-4" />}
              label={t("cognition.patterns", "Patterns")}
              value={(s.artifact_status as Record<string, number>)?.patterns_written ?? 0}
              color="text-purple-500"
            />
          </div>

          {/* Uncertainty Stats */}
          {s.enabled && s.uncertainty_stats.avg > 0 && (
            <div className="rounded-lg border border-border/60 bg-background/50 p-3">
              <p className="text-xs font-black uppercase text-muted-foreground">
                {t("cognition.uncertaintyStats", "Uncertainty Stats")}
              </p>
              <div className="mt-2 flex gap-4 text-sm">
                <span>
                  {t("cognition.avgUncertainty", "Avg")}: {(s.uncertainty_stats.avg * 100).toFixed(1)}%
                </span>
                <span>
                  {t("cognition.maxUncertainty", "Max")}: {(s.uncertainty_stats.max * 100).toFixed(1)}%
                </span>
                <span>
                  {t("cognition.highUncertainty", "High")}: {s.uncertainty_stats.high_count}
                  <span className="ml-1 text-xs text-muted-foreground">
                    (\u2265{Math.round(s.uncertainty_stats.threshold * 100)}%)
                  </span>
                </span>
              </div>
            </div>
          )}

          {/* Suppression Reasons */}
          {suppressionReasons.length > 0 && totalDecision > 0 && (
            <div className="rounded-lg border border-border/60 bg-background/50 p-3">
              <p className="text-xs font-black uppercase text-muted-foreground">
                {t("cognition.suppressionBreakdown", "Suppression Breakdown")}
              </p>
              <div className="mt-2 space-y-1.5">
                {suppressionReasons.map(([reason, count]) => (
                  <div key={reason} className="flex items-center gap-2">
                    <div className="h-2 flex-1 rounded-full bg-muted">
                      <div
                        className="h-2 rounded-full bg-amber-400"
                        style={{ width: `${(count / totalDecision) * 100}%` }}
                      />
                    </div>
                    <span className="w-12 text-right text-xs text-muted-foreground">
                      {count}
                    </span>
                    <span className="w-32 truncate text-xs text-muted-foreground">
                      {reason.replace(/_/g, " ")}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Section: Recent Triggers */}
          <CollapsibleSection
            title={t("cognition.recentTriggers", "Recent Triggers")}
            count={s.recent_triggers.length}
            expanded={expandedSection === "triggers"}
            onToggle={() => toggleSection("triggers")}
          >
            <TriggerTimeline triggers={s.recent_triggers} />
          </CollapsibleSection>

          {/* Section: Recent Reflections */}
          <CollapsibleSection
            title={t("cognition.recentReflections", "Recent Reflections")}
            count={s.recent_reflections.length}
            expanded={expandedSection === "reflections"}
            onToggle={() => toggleSection("reflections")}
          >
            <div className="space-y-3">
              {s.recent_reflections.map((reflection) => (
                <ReflectionCard key={reflection.reflection_id} reflection={reflection} />
              ))}
            </div>
          </CollapsibleSection>

          {/* Section: Error Patterns */}
          <CollapsibleSection
            title={t("cognition.errorPatterns", "Error Patterns")}
            count={s.recent_patterns.length}
            expanded={expandedSection === "patterns"}
            onToggle={() => toggleSection("patterns")}
          >
            <PatternTable patterns={s.recent_patterns} />
          </CollapsibleSection>

          {/* Section: Bridge Stats */}
          <CollapsibleSection
            title={t("cognition.bridgeStats", "Bridge Stats")}
            expanded={expandedSection === "bridges"}
            onToggle={() => toggleSection("bridges")}
          >
            <BridgeStats
              workingMemory={s.working_memory_bridge}
              memoryCandidate={s.memory_candidate_bridge}
              fastPath={s.fast_path_decision_counts}
            />
          </CollapsibleSection>
        </div>
      </ScrollArea>
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/50 p-4">
      <div className="flex items-center gap-2 text-muted-foreground">
        <span className={color}>{icon}</span>
        <span className="text-xs font-medium">{label}</span>
      </div>
      <p className={`mt-1 text-2xl font-black ${color}`}>{value}</p>
    </div>
  );
}

function CollapsibleSection({
  title,
  count,
  expanded,
  onToggle,
  children,
}: {
  title: string;
  count?: number;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-card">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
          <span className="text-sm font-black text-foreground">{title}</span>
          {count !== undefined && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black text-muted-foreground">
              {count}
            </span>
          )}
        </div>
      </button>
      {expanded && (
        <>
          <Separator />
          <div className="p-4">{children}</div>
        </>
      )}
    </div>
  );
}

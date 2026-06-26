import { useTranslation } from "react-i18next";
import type { OpportunitySignal } from "@/lib/types";
import { Button } from "@/components/ui/button";

interface SignalCardProps {
  signal: OpportunitySignal;
  onSuppress: (id: string) => void;
  onResume: (id: string) => void;
  disabled?: boolean;
}

export function SignalCard({ signal, onSuppress, onResume, disabled }: SignalCardProps) {
  const { t } = useTranslation();

  const kindLabel = signal.kind === "workflow_candidate"
    ? t("signals.workflow", "Workflow")
    : t("signals.skill", "Skill");

  return (
    <div className={`rounded-lg border p-4 ${
      signal.status === "converted"
        ? "border-emerald-300 bg-emerald-50/50 dark:border-emerald-800 dark:bg-emerald-950/10"
        : signal.status === "suppressed"
          ? "border-gray-300 bg-gray-50/50 dark:border-gray-700 dark:bg-gray-950/10"
          : "border-border/70 bg-background/55"
    }`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black uppercase text-muted-foreground">
              {kindLabel}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
              signal.risk_level === "high"
                ? "bg-red-100 text-red-700 dark:bg-red-950/30 dark:text-red-400"
                : "bg-amber-100 text-amber-700 dark:bg-amber-950/30 dark:text-amber-400"
            }`}>
              {signal.risk_level}
            </span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
              signal.status === "open"
                ? "bg-blue-100 text-blue-700 dark:bg-blue-950/30 dark:text-blue-400"
                : signal.status === "converted"
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-muted text-muted-foreground"
            }`}>
              {signal.status}
            </span>
          </div>
          <p className="mt-2 text-sm font-black text-foreground">{signal.title}</p>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
            {signal.summary}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>{t("signals.seenCount", "Seen {{count}} times", { count: signal.seen_count })}</span>
            <span>{t("signals.priority", "Priority")}: {(signal.priority_score * 100).toFixed(0)}%</span>
            <span>{t("signals.evidenceCount", "Evidence")}: {signal.evidence_sources.length}</span>
            {signal.converted_proposal_id && (
              <span className="font-black text-emerald-600">
                {t("signals.converted", "Converted")}
              </span>
            )}
            {signal.suppression_reason && (
              <span className="text-amber-600">
                {signal.suppression_reason}
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 gap-1.5">
          {signal.status === "open" && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onSuppress(signal.opportunity_id)}
              disabled={disabled}
              className="rounded-full text-xs"
            >
              {t("signals.suppress", "Suppress")}
            </Button>
          )}
          {signal.status === "suppressed" && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onResume(signal.opportunity_id)}
              disabled={disabled}
              className="rounded-full text-xs"
            >
              {t("signals.resume", "Resume")}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

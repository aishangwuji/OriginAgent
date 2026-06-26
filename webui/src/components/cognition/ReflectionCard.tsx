import { useTranslation } from "react-i18next";
import type { ReflectionRecord } from "@/lib/types";

function confidencePercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

const RETENTION_COLORS: Record<string, string> = {
  candidate: "border-l-emerald-500",
  review: "border-l-amber-500",
  short: "border-l-blue-500",
  discard: "border-l-gray-400",
};

const RETENTION_BG: Record<string, string> = {
  candidate: "bg-emerald-50 dark:bg-emerald-950/20",
  review: "bg-amber-50 dark:bg-amber-950/20",
  short: "bg-blue-50 dark:bg-blue-950/20",
  discard: "bg-gray-50 dark:bg-gray-950/20",
};

export function ReflectionCard({ reflection }: { reflection: ReflectionRecord }) {
  const { t } = useTranslation();
  const retention = reflection.retention_hint || "discard";
  const uncertainty = reflection.payload?.uncertainty_score as number | undefined;

  return (
    <div
      className={`rounded-lg border border-border/60 border-l-4 p-3 ${
        RETENTION_BG[retention] ?? "bg-background/50"
      } ${RETENTION_COLORS[retention] ?? "border-l-gray-400"}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black uppercase text-muted-foreground">
          {reflection.reflection_kind || "reflection"}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {t("cognition.confidence", "Confidence")}: {confidencePercent(reflection.confidence)}
          {uncertainty !== undefined && (
            <span className="ml-2">
              {t("cognition.uncertainty", "Uncertainty")}: {confidencePercent(uncertainty)}
            </span>
          )}
        </span>
      </div>
      <p className="mt-2 text-sm font-semibold leading-snug text-foreground">
        {reflection.summary || t("cognition.noSummary", "No summary")}
      </p>
      {reflection.root_cause_hypotheses && reflection.root_cause_hypotheses.length > 0 && (
        <div className="mt-2">
          <p className="text-xs font-black uppercase text-muted-foreground">
            {t("cognition.rootCause", "Root Cause Hypotheses")}
          </p>
          <ul className="mt-1 space-y-0.5">
            {reflection.root_cause_hypotheses.map((cause, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-muted-foreground/50" />
                {cause}
              </li>
            ))}
          </ul>
        </div>
      )}
      {reflection.what_failed && reflection.what_failed.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {reflection.what_failed.map((item, i) => (
            <span
              key={i}
              className="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] font-black text-destructive"
            >
              {item}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

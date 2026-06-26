import { useTranslation } from "react-i18next";
import type { PatternRecord } from "@/lib/types";

function scoreColor(score: number): string {
  if (score >= 0.7) return "text-red-500";
  if (score >= 0.4) return "text-amber-500";
  return "text-muted-foreground";
}

export function PatternTable({ patterns }: { patterns: PatternRecord[] }) {
  const { t } = useTranslation();
  if (patterns.length === 0) {
    return (
      <div className="px-2 py-8 text-center">
        <p className="text-sm text-muted-foreground">
          {t("cognition.noPatterns", "No patterns consolidated yet.")}
        </p>
        <p className="mt-1 text-xs text-muted-foreground/70">
          {t("cognition.noPatternsHint", "Error patterns emerge when similar reflections are grouped by the consolidation engine. Enable Pattern Consolidation in Learning settings to start detecting patterns.")}
        </p>
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/60 text-left text-[11px] font-black uppercase text-muted-foreground">
            <th className="pb-2 pr-3">{t("cognition.patternSummary", "Summary")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternDomain", "Domain")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternSeverity", "Severity")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternFrequency", "Freq")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternTurns", "Turns")}</th>
            <th className="pb-2 pr-3">{t("cognition.patternScore", "Score")}</th>
            <th className="pb-2">{t("cognition.patternTarget", "Target")}</th>
          </tr>
        </thead>
        <tbody>
          {patterns.map((pattern) => (
            <tr key={pattern.pattern_id} className="border-b border-border/40">
              <td className="py-2 pr-3 font-medium text-foreground">
                {pattern.summary}
              </td>
              <td className="py-2 pr-3 text-muted-foreground">
                {pattern.capability_domain}
              </td>
              <td className="py-2 pr-3">
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
                    pattern.severity === "high"
                      ? "bg-red-100 text-red-700 dark:bg-red-950/30 dark:text-red-400"
                      : pattern.severity === "medium"
                        ? "bg-amber-100 text-amber-700 dark:bg-amber-950/30 dark:text-amber-400"
                        : "bg-muted text-muted-foreground"
                  }`}
                >
                  {pattern.severity}
                </span>
              </td>
              <td className="py-2 pr-3 text-muted-foreground">{pattern.frequency}</td>
              <td className="py-2 pr-3 text-muted-foreground">
                {pattern.distinct_turn_count}
              </td>
              <td className={`py-2 pr-3 font-black ${scoreColor(pattern.pattern_score)}`}>
                {pattern.pattern_score.toFixed(2)}
              </td>
              <td className="py-2 text-muted-foreground">
                {pattern.candidate_target_type || "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

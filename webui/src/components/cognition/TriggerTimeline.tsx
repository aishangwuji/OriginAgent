import { useTranslation } from "react-i18next";
import type { MetaTriggerRecord } from "@/lib/types";
import { cn } from "@/lib/utils";

const TRIGGER_COLORS: Record<string, string> = {
  tool_failure: "bg-red-500",
  user_correction: "bg-amber-500",
  task_completion: "bg-emerald-500",
};

const TRIGGER_ICONS: Record<string, string> = {
  tool_failure: "\u{1F534}",
  user_correction: "\u{1F7E1}",
  task_completion: "\u{1F7E2}",
};

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

export function TriggerTimeline({ triggers }: { triggers: MetaTriggerRecord[] }) {
  const { t } = useTranslation();
  if (triggers.length === 0) {
    return (
      <div className="px-2 py-8 text-center">
        <p className="text-sm text-muted-foreground">
          {t("cognition.noTriggers", "No triggers recorded yet.")}
        </p>
        <p className="mt-1 text-xs text-muted-foreground/70">
          {t("cognition.noTriggersHint", "Triggers are collected when meta-cognition is enabled in Learning settings. Tool failures, user corrections, and task completions will appear here.")}
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {triggers.map((trigger) => (
        <div
          key={trigger.trigger_id}
          className="flex items-start gap-3 rounded-lg border border-border/60 bg-background/50 p-3 text-sm"
        >
          <span className="mt-0.5 shrink-0 text-base leading-none" aria-hidden>
            {TRIGGER_ICONS[trigger.trigger_type] ?? "\u26AA"}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-block h-2 w-2 rounded-full",
                  TRIGGER_COLORS[trigger.trigger_type] ?? "bg-gray-400",
                )}
              />
              <span className="font-black text-foreground">
                {trigger.trigger_type}
              </span>
              <span className="text-xs text-muted-foreground">
                {trigger.severity}
              </span>
            </div>
            <p className="mt-0.5 truncate text-muted-foreground">
              {trigger.source_reference}
            </p>
            {typeof trigger.payload?.tool_name === "string" && (
              <p className="text-xs text-muted-foreground">
                tool: {trigger.payload.tool_name}
                {typeof trigger.payload.status === "string" ? ` \u00B7 ${trigger.payload.status}` : ""}
              </p>
            )}
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">
            {timeAgo(trigger.created_at)}
          </span>
        </div>
      ))}
    </div>
  );
}

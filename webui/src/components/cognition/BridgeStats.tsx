import { useTranslation } from "react-i18next";
import type { BridgeStatus } from "@/lib/types";

function BridgeCard({
  title,
  status,
}: {
  title: string;
  status: BridgeStatus | undefined;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/50 p-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-black text-foreground">{title}</p>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
            status?.enabled
              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-400"
              : "bg-muted text-muted-foreground"
          }`}
        >
          {status?.enabled ? "Enabled" : "Disabled"}
        </span>
      </div>
      {status && status.enabled && status.decision_counts && (
        <div className="mt-2 flex flex-wrap gap-2">
          {Object.entries(status.decision_counts).map(([key, value]) => (
            <span
              key={key}
              className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black text-muted-foreground"
            >
              {key}: {value}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function BridgeStats({
  workingMemory,
  memoryCandidate,
  fastPath,
}: {
  workingMemory: BridgeStatus;
  memoryCandidate: BridgeStatus;
  fastPath: Record<string, number>;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-3">
      <BridgeCard
        title={t("cognition.workingMemoryBridge", "Working Memory Bridge")}
        status={workingMemory}
      />
      <BridgeCard
        title={t("cognition.memoryCandidateBridge", "Memory Candidate Bridge")}
        status={memoryCandidate}
      />
      {Object.keys(fastPath).length > 0 && (
        <div className="rounded-lg border border-border/60 bg-background/50 p-3">
          <p className="text-sm font-black text-foreground">
            {t("cognition.fastPath", "Fast Path")}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(fastPath).map(([key, value]) => (
              <span
                key={key}
                className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-black text-muted-foreground"
              >
                {key}: {value}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

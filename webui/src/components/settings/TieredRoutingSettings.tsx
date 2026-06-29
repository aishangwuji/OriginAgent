import { useTranslation } from "react-i18next";
import { BooleanSwitch, SettingsGroup, SettingsRow, SettingsSectionTitle } from "./shared";

interface TieredRoutingSettingsProps {
  enabled: boolean;
  defaultTier: string;
  onChange: (patch: { enabled?: boolean; default_tier?: string }) => void;
}

const TIER_LABELS: Record<string, string> = {
  economy: "🟢 Economy — 高性价比（DeepSeek / Gemini Flash）",
  standard: "🔵 Standard — 均衡（Claude Sonnet）",
  premium: "🟣 Premium — 旗舰（Claude Opus / Fable）",
};

export function TieredRoutingSettings({
  enabled,
  defaultTier,
  onChange,
}: TieredRoutingSettingsProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-8">
      {/* Overview */}
      <section>
        <SettingsSectionTitle>{t("settings.tieredRouter.overview")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.tieredRouter.enable")}
            description={t("settings.tieredRouter.enableHelp")}
          >
            <BooleanSwitch
              checked={enabled}
              ariaLabel={t("settings.tieredRouter.enable")}
              onChange={(checked) => onChange({ enabled: checked })}
            />
          </SettingsRow>
        </SettingsGroup>
      </section>

      {/* Tier definitions */}
      <section>
        <SettingsSectionTitle>{t("settings.tieredRouter.tiers")}</SettingsSectionTitle>
        <SettingsGroup>
          {Object.entries(TIER_LABELS).map(([tierName, label]) => {
            const isDefault = tierName === defaultTier;
            const isActive = enabled && isDefault;
            return (
              <div
                key={tierName}
                className="flex items-center gap-4 px-5 py-3.5"
              >
                <span className="min-w-0 flex-1 text-[14px] leading-5 text-foreground">
                  {label}
                  {isDefault ? (
                    <span className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
                      {t("settings.tieredRouter.default")}
                    </span>
                  ) : null}
                </span>
                {isActive ? (
                  <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-500/15">
                    <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
                  </span>
                ) : null}
              </div>
            );
          })}
        </SettingsGroup>
      </section>

      {/* Task → Tier mapping reference */}
      <section>
        <SettingsSectionTitle>{t("settings.tieredRouter.mapping")}</SettingsSectionTitle>
        <SettingsGroup>
          <div className="divide-y divide-border/45">
            <MappingRow task="web_search" tier="economy" enabled={enabled} />
            <MappingRow task="consolidation" tier="economy" enabled={enabled} />
            <MappingRow task="webui_title" tier="economy" enabled={enabled} />
            <MappingRow task="snapshot_inspection" tier="economy" enabled={enabled} />
            <MappingRow task="dream_phase1" tier="economy" enabled={enabled} />
            <MappingRow task="background_review" tier="standard" enabled={enabled} />
            <MappingRow task="meta_cognition" tier="standard" enabled={enabled} />
            <MappingRow task="dream_phase2" tier="standard" enabled={enabled} />
            <MappingRow task="bdi_deliberation" tier="standard" enabled={enabled} />
            <MappingRow task="heartbeat" tier="premium" enabled={enabled} />
            <MappingRow task="evaluator" tier="premium" enabled={enabled} />
          </div>
        </SettingsGroup>
      </section>
    </div>
  );
}

function MappingRow({
  task,
  tier,
  enabled,
}: {
  task: string;
  tier: string;
  enabled: boolean;
}) {
  const tierColors: Record<string, string> = {
    economy: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    standard: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
    premium: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  };

  return (
    <div className="flex items-center gap-3 px-5 py-2.5">
      <code className="min-w-0 flex-1 text-[13px] font-mono text-foreground/75">
        {task}
      </code>
      <span
        className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide transition-opacity ${
          tierColors[tier] ?? "bg-muted text-muted-foreground"
        } ${enabled ? "" : "opacity-50"}`}
      >
        {tier}
      </span>
    </div>
  );
}

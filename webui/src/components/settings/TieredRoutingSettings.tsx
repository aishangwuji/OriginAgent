import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";
import { BooleanSwitch, SettingsGroup, SettingsRow, SettingsSectionTitle } from "./shared";

// 可用层级顺序
const TIER_ORDER: string[] = ["economy", "standard", "premium"];

// 任务 → 层级映射（后端内部标识符）
const TASK_MAPPING: Array<{ task: string; tier: string }> = [
  { task: "web_search", tier: "economy" },
  { task: "consolidation", tier: "economy" },
  { task: "webui_title", tier: "economy" },
  { task: "snapshot_inspection", tier: "economy" },
  { task: "dream_phase1", tier: "economy" },
  { task: "background_review", tier: "standard" },
  { task: "meta_cognition", tier: "standard" },
  { task: "dream_phase2", tier: "standard" },
  { task: "bdi_deliberation", tier: "standard" },
  { task: "heartbeat", tier: "premium" },
  { task: "evaluator", tier: "premium" },
];

interface TieredRoutingSettingsProps {
  enabled: boolean;
  defaultTier: string;
  tiers: Record<string, { provider: string; model: string }>;
  providers: Array<{ name: string; label: string; configured: boolean }>;
  onChange: (patch: {
    enabled?: boolean;
    default_tier?: string;
    tiers?: Record<string, { provider: string; model: string }>;
  }) => void;
  onFetchProviderModels: (provider: string) => Promise<string[]>;
}

export function TieredRoutingSettings({
  enabled,
  defaultTier,
  tiers,
  providers,
  onChange,
  onFetchProviderModels,
}: TieredRoutingSettingsProps) {
  const { t } = useTranslation();
  const configuredProviders = providers.filter((p) => p.configured);

  return (
    <div className="space-y-8">
      {/* 总览与开关 */}
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

      {/* 层级定义：每层可选 provider + model */}
      <section>
        <SettingsSectionTitle>{t("settings.tieredRouter.tiers")}</SettingsSectionTitle>
        <SettingsGroup>
          {TIER_ORDER.map((tierName) => (
            <TierEditor
              key={tierName}
              tierName={tierName}
              tier={tiers[tierName]}
              isDefault={tierName === defaultTier}
              enabled={enabled}
              providers={configuredProviders}
              onChange={(provider, model) =>
                onChange({
                  tiers: { ...tiers, [tierName]: { provider, model } },
                })
              }
              onSetDefault={() => onChange({ default_tier: tierName })}
              onFetchProviderModels={onFetchProviderModels}
            />
          ))}
        </SettingsGroup>
      </section>

      {/* 任务 → 层级映射（只读参考） */}
      <section>
        <SettingsSectionTitle>{t("settings.tieredRouter.mapping")}</SettingsSectionTitle>
        <SettingsGroup>
          <div className="divide-y divide-border/45">
            {TASK_MAPPING.map(({ task, tier }) => (
              <MappingRow key={task} task={task} tier={tier} enabled={enabled} />
            ))}
          </div>
        </SettingsGroup>
      </section>
    </div>
  );
}

/** 单个层级的编辑器：层级名 + provider 下拉 + model 下拉（懒加载 catalog）。 */
function TierEditor({
  tierName,
  tier,
  isDefault,
  enabled,
  providers,
  onChange,
  onSetDefault,
  onFetchProviderModels,
}: {
  tierName: string;
  tier: { provider: string; model: string } | undefined;
  isDefault: boolean;
  enabled: boolean;
  providers: Array<{ name: string; label: string }>;
  onChange: (provider: string, model: string) => void;
  onSetDefault: () => void;
  onFetchProviderModels: (provider: string) => Promise<string[]>;
}) {
  const { t } = useTranslation();
  const provider = tier?.provider ?? "";
  const model = tier?.model ?? "";

  // 每个 provider 的模型目录缓存（懒加载）
  const [catalogs, setCatalogs] = useState<Record<string, string[]>>({});
  const [loadingProvider, setLoadingProvider] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const refreshCatalog = useCallback(
    async (providerName: string) => {
      if (!providerName || catalogs[providerName]) return;
      setLoadingProvider(providerName);
      setFetchError(null);
      try {
        const models = await onFetchProviderModels(providerName);
        setCatalogs((prev) => ({ ...prev, [providerName]: models }));
      } catch (err) {
        setFetchError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoadingProvider(null);
      }
    },
    [catalogs, onFetchProviderModels],
  );

  // 选择 provider 时自动拉取该 provider 的模型目录
  useEffect(() => {
    if (provider && !catalogs[provider] && loadingProvider !== provider) {
      refreshCatalog(provider);
    }
  }, [provider, catalogs, loadingProvider, refreshCatalog]);

  const tierColors: Record<string, string> = {
    economy: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    standard: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
    premium: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  };

  return (
    <div className="px-5 py-4">
      {/* 层级名 + 默认标记 */}
      <div className="mb-3 flex items-center gap-2">
        <span
          className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide ${
            tierColors[tierName] ?? "bg-muted text-muted-foreground"
          } ${enabled ? "" : "opacity-50"}`}
        >
          {t(`settings.tieredRouter.tier.${tierName}`)}
        </span>
        {isDefault ? (
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
            {t("settings.tieredRouter.default")}
          </span>
        ) : (
          <button
            type="button"
            onClick={onSetDefault}
            className="text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {t("settings.tieredRouter.setAsDefault")}
          </button>
        )}
      </div>

      {/* provider + model 选择 */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className="text-[12px] font-medium text-muted-foreground">
            {t("settings.tieredRouter.providerLabel")}
          </span>
          <select
            value={provider}
            onChange={(e) => onChange(e.target.value, "")}
            className="h-9 rounded-[10px] border border-border/60 bg-background px-3 text-[13px] outline-none transition-colors focus:border-primary"
          >
            <option value="">
              {t("settings.tieredRouter.providerPlaceholder")}
            </option>
            {providers.map((p) => (
              <option key={p.name} value={p.name}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-[12px] font-medium text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              {t("settings.tieredRouter.modelLabel")}
              {provider && (
                <button
                  type="button"
                  onClick={() => refreshCatalog(provider)}
                  disabled={loadingProvider === provider}
                  className="text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                  aria-label={t("settings.tieredRouter.refreshModels")}
                  title={t("settings.tieredRouter.refreshModels")}
                >
                  {loadingProvider === provider ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                  ) : (
                    <RefreshCw className="h-3 w-3" aria-hidden />
                  )}
                </button>
              )}
            </span>
          </span>
          <ModelInput
            value={model}
            catalog={provider ? catalogs[provider] : undefined}
            placeholder={t("settings.tieredRouter.modelPlaceholder")}
            onChange={(v) => onChange(provider, v)}
          />
        </label>
      </div>

      {fetchError && provider && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {t("settings.tieredRouter.catalogFetchFailed")}: {fetchError}
        </p>
      )}
    </div>
  );
}

/** model 输入：有 catalog 时用 datalist 提供建议，允许自由输入。 */
function ModelInput({
  value,
  catalog,
  placeholder,
  onChange,
}: {
  value: string;
  catalog: string[] | undefined;
  placeholder: string;
  onChange: (value: string) => void;
}) {
  const listId = "tier-model-list";
  return (
    <>
      <input
        type="text"
        list={catalog && catalog.length > 0 ? listId : undefined}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 rounded-[10px] border border-border/60 bg-background px-3 text-[13px] outline-none transition-colors focus:border-primary"
      />
      {catalog && catalog.length > 0 && (
        <datalist id={listId}>
          {catalog.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      )}
    </>
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
  const { t } = useTranslation();
  const tierColors: Record<string, string> = {
    economy: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    standard: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
    premium: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  };

  return (
    <div className="flex items-center gap-3 px-5 py-2.5">
      <span className="min-w-0 flex-1 text-[13px] text-foreground/80">
        {t(`settings.tieredRouter.task.${task}`)}
      </span>
      <span
        className={`rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide transition-opacity ${
          tierColors[tier] ?? "bg-muted text-muted-foreground"
        } ${enabled ? "" : "opacity-50"}`}
      >
        {t(`settings.tieredRouter.tier.${tier}`)}
      </span>
    </div>
  );
}

import { useTranslation } from "react-i18next";

interface LearningSettingsProps {
  // Meta-cognition toggles
  enabled: boolean;
  triggerCollectionEnabled: boolean;
  structuredReflectionEnabled: boolean;
  patternConsolidationEnabled: boolean;
  evolutionBridgeEnabled: boolean;
  workingMemoryBridgeEnabled: boolean;
  memoryCandidateBridgeEnabled: boolean;
  onToggleMetaCognition: (checked: boolean) => void;
  onToggleTriggerCollection: (checked: boolean) => void;
  onToggleStructuredReflection: (checked: boolean) => void;
  onTogglePatternConsolidation: (checked: boolean) => void;
  onToggleEvolutionBridge: (checked: boolean) => void;
  onToggleWorkingMemoryBridge: (checked: boolean) => void;
  onToggleMemoryCandidateBridge: (checked: boolean) => void;
  // Background review
  backgroundReviewEnabled: boolean;
  backgroundReviewSaving: boolean;
  onToggleBackgroundReview: (checked: boolean) => void;
  // Curator
  curatorEnabled: boolean;
  onToggleCurator: (checked: boolean) => void;
  // Dream
  dreamAnnotateLineAges: boolean;
  onToggleDreamAnnotateLineAges: (checked: boolean) => void;
}

function BooleanSwitch({
  checked,
  ariaLabel,
  disabled,
  onChange,
}: {
  checked: boolean;
  ariaLabel: string;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-label={ariaLabel}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`inline-flex h-7 w-12 items-center rounded-full p-0.5 transition-colors ${
        checked ? "bg-primary" : "bg-muted"
      } ${disabled ? "opacity-60" : ""}`}
    >
      <span
        className={`h-6 w-6 rounded-full bg-background shadow-sm transition-transform ${
          checked ? "translate-x-5" : ""
        }`}
      />
    </button>
  );
}

function SettingsGroup({ children }: { children: React.ReactNode }) {
  return (
    <div className="divide-y divide-border/60 overflow-hidden rounded-2xl border border-border/60 bg-card shadow-sm">
      {children}
    </div>
  );
}

function SettingsRow({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-5 py-3.5">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-black text-foreground">{title}</p>
        {description ? (
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function SettingsSectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="px-1 pb-2 text-[13px] font-semibold uppercase tracking-wider text-muted-foreground/80">
      {children}
    </h3>
  );
}

export function LearningSettings(props: LearningSettingsProps) {
  const { t } = useTranslation();

  return (
    <div className="space-y-8">
      {/* Meta-Cognition */}
      <section>
        <SettingsSectionTitle>{t("settings.sections.metaCognition", "Meta-Cognition")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.metaCognition", "Meta-Cognition")}
            description={t("settings.help.metaCognition", "Enable the agent's self-reflection pipeline. When disabled, no triggers are collected and no reflections are generated.")}
          >
            <BooleanSwitch
              checked={props.enabled}
              ariaLabel="Meta-Cognition"
              onChange={props.onToggleMetaCognition}
            />
          </SettingsRow>
          {props.enabled && (
            <>
              <SettingsRow
                title={t("settings.rows.triggerCollection", "Trigger Collection")}
                description={t("settings.help.triggerCollection", "Collect tool failures, user corrections, and task completions as reflection triggers.")}
              >
                <BooleanSwitch
                  checked={props.triggerCollectionEnabled}
                  ariaLabel="Trigger Collection"
                  onChange={props.onToggleTriggerCollection}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.structuredReflection", "Structured Reflection")}
                description={t("settings.help.structuredReflection", "Use an LLM to analyze triggers and generate structured reflections with root cause hypotheses and learned rules.")}
              >
                <BooleanSwitch
                  checked={props.structuredReflectionEnabled}
                  ariaLabel="Structured Reflection"
                  onChange={props.onToggleStructuredReflection}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.patternConsolidation", "Pattern Consolidation")}
                description={t("settings.help.patternConsolidation", "Group similar reflections into error patterns for trend analysis and evolution targeting.")}
              >
                <BooleanSwitch
                  checked={props.patternConsolidationEnabled}
                  ariaLabel="Pattern Consolidation"
                  onChange={props.onTogglePatternConsolidation}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.evolutionBridge", "Evolution Bridge")}
                description={t("settings.help.evolutionBridge", "Bridge error patterns to the evolution engine as opportunity signals for workflow/skill candidates.")}
              >
                <BooleanSwitch
                  checked={props.evolutionBridgeEnabled}
                  ariaLabel="Evolution Bridge"
                  onChange={props.onToggleEvolutionBridge}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.workingMemoryBridge", "Working Memory Bridge")}
                description={t("settings.help.workingMemoryBridge", "Inject reflection insights into the agent's working memory.")}
              >
                <BooleanSwitch
                  checked={props.workingMemoryBridgeEnabled}
                  ariaLabel="Working Memory Bridge"
                  onChange={props.onToggleWorkingMemoryBridge}
                />
              </SettingsRow>
              <SettingsRow
                title={t("settings.rows.memoryCandidateBridge", "Memory Candidate Bridge")}
                description={t("settings.help.memoryCandidateBridge", "Write high-confidence learned rules as memory candidates for long-term retention.")}
              >
                <BooleanSwitch
                  checked={props.memoryCandidateBridgeEnabled}
                  ariaLabel="Memory Candidate Bridge"
                  onChange={props.onToggleMemoryCandidateBridge}
                />
              </SettingsRow>
            </>
          )}
        </SettingsGroup>
      </section>

      {/* Background Review & Curator */}
      <section>
        <SettingsSectionTitle>{t("settings.sections.backgroundReview", "Background Review")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.backgroundReview", "Background Review")}
            description={t("settings.help.backgroundReview", "Generate reviewable learning proposals from conversation history after successful turns.")}
          >
            <BooleanSwitch
              checked={props.backgroundReviewEnabled}
              ariaLabel="Background Review"
              disabled={props.backgroundReviewSaving}
              onChange={props.onToggleBackgroundReview}
            />
          </SettingsRow>
          <SettingsRow
            title={t("settings.rows.curator", "Curator")}
            description={t("settings.help.curator", "Enable deterministic curator proposal generation for structured learning.")}
          >
            <BooleanSwitch
              checked={props.curatorEnabled}
              ariaLabel="Curator"
              onChange={props.onToggleCurator}
            />
          </SettingsRow>
        </SettingsGroup>
      </section>

      {/* Dream Memory */}
      <section>
        <SettingsSectionTitle>{t("settings.sections.dream", "Dream Memory")}</SettingsSectionTitle>
        <SettingsGroup>
          <SettingsRow
            title={t("settings.rows.dreamAnnotateLineAges", "Dream Line-Age Annotation")}
            description={t("settings.help.dreamAnnotateLineAges", "Annotate code lines with age hints during Dream consolidation to help the model understand content freshness.")}
          >
            <BooleanSwitch
              checked={props.dreamAnnotateLineAges}
              ariaLabel="Dream Line-Age Annotation"
              onChange={props.onToggleDreamAnnotateLineAges}
            />
          </SettingsRow>
        </SettingsGroup>
      </section>
    </div>
  );
}

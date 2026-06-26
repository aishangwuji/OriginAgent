import { type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  Brain,
  Cloud,
  Cpu,
  Database,
  Gem,
  Grid3X3,
  Hexagon,
  Layers,
  Moon,
  Orbit,
  Waves,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// -- provider icon -----------------------------------------------------------

const PROVIDER_ICONS: Record<string, React.ComponentType<{ className?: string; strokeWidth?: number; "aria-hidden"?: boolean }>> = {
  openai: Brain,
  anthropic: Brain,
  openrouter: Layers,
  deepseek: Waves,
  zhipu: Grid3X3,
  dashscope: Cloud,
  moonshot: Moon,
  minimax: Zap,
  minimax_anthropic: Brain,
  groq: Cpu,
  huggingface: Layers,
  gemini: Gem,
  mistral: Orbit,
  siliconflow: Layers,
  volcengine: Cloud,
  volcengine_coding_plan: Cloud,
  byteplus: Cloud,
  byteplus_coding_plan: Cloud,
  qianfan: Database,
  azure_openai: Cloud,
  bedrock: Database,
};

export function ProviderIcon({ provider }: { provider: string }) {
  const Icon = PROVIDER_ICONS[provider] ?? Hexagon;
  return (
    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-muted text-foreground/82 shadow-[inset_0_0_0_1px_rgba(0,0,0,0.025)] dark:bg-muted/70">
      <Icon className="h-5 w-5" strokeWidth={2} aria-hidden />
    </span>
  );
}

// -- layout primitives -------------------------------------------------------

export function SettingsSectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="mb-2 px-1 text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
      {children}
    </h2>
  );
}

export function SettingsGroup({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]">
      <div className="divide-y divide-border/45">{children}</div>
    </div>
  );
}

export function SettingsRow({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex min-h-[62px] flex-col gap-3 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="min-w-0">
        <div className="text-[14px] font-medium leading-5 text-foreground">{title}</div>
        {description ? (
          <div className="mt-0.5 max-w-[28rem] text-[12px] leading-5 text-muted-foreground">
            {description}
          </div>
        ) : null}
      </div>
      {children ? <div className="shrink-0 sm:ml-6">{children}</div> : null}
    </div>
  );
}

export function SettingsFooter({
  dirty,
  saving,
  saved,
  onSave,
}: {
  dirty: boolean;
  saving: boolean;
  saved: boolean;
  onSave: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-[58px] items-center justify-between gap-4 px-4 py-3 sm:px-5">
      <div className="text-[13px] text-muted-foreground">
        {saved ? t("settings.status.savedRestart") : t("settings.status.unsaved")}
      </div>
      <Button size="sm" variant="outline" onClick={onSave} disabled={!dirty || saving} className="rounded-full">
        {saving ? t("settings.actions.saving") : t("settings.actions.save")}
      </Button>
    </div>
  );
}

// -- interactive controls ----------------------------------------------------

export function BooleanSwitch({
  checked,
  onChange,
  disabled = false,
  ariaLabel,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "inline-flex h-7 w-12 items-center rounded-full p-0.5 transition-colors",
        checked ? "bg-primary" : "bg-muted",
        disabled && "opacity-60",
      )}
    >
      <span
        className={cn(
          "h-6 w-6 rounded-full bg-background shadow-sm transition-transform",
          checked && "translate-x-5",
        )}
      />
    </button>
  );
}

export function SimpleSelect({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-8 min-w-[180px] rounded-full border border-border bg-background px-3 text-[13px]"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

// -- additional settings widgets ---------------------------------------------

export function TextareaRow({
  title,
  description,
  value,
  placeholder,
  onChange,
}: {
  title: string;
  description?: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  return (
    <SettingsRow title={title} description={description}>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="min-h-[88px] w-[280px] max-w-full resize-y rounded-[16px] border border-input bg-background px-3 py-2 text-[13px] text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
      />
    </SettingsRow>
  );
}

export function ByokSectionHeader({
  title,
  count,
}: {
  title: string;
  count: number;
}) {
  return (
    <div className="mb-1.5 flex items-center gap-2 px-1">
      <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
        {title}
      </h2>
      <span className="rounded-full bg-muted px-2 py-0.5 text-[11.5px] font-medium text-muted-foreground">
        {count}
      </span>
    </div>
  );
}

export function ByokEmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-[18px] border border-dashed border-border/65 bg-card/45 px-4 py-5 text-[13px] text-muted-foreground">
      {children}
    </div>
  );
}

import { LoaderCircle, Menu, Moon, Sun, Target } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { GoalStateWsPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ThreadHeaderProps {
  title: string;
  onToggleSidebar: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  goalState?: GoalStateWsPayload;
  runStartedAt?: number | null;
  hideSidebarToggleOnDesktop?: boolean;
  minimal?: boolean;
}

export function ThreadHeader({
  title,
  onToggleSidebar,
  theme,
  onToggleTheme,
  goalState,
  runStartedAt = null,
  hideSidebarToggleOnDesktop = false,
  minimal = false,
}: ThreadHeaderProps) {
  const { t } = useTranslation();
  const activeGoal = goalState?.active
    ? (goalState.ui_summary || goalState.objective || "").trim()
    : "";
  if (minimal) {
    return (
      <div className="relative z-10 flex h-11 items-center justify-between gap-3 px-3 py-2">
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("thread.header.toggleSidebar")}
          onClick={onToggleSidebar}
          className={cn(
            "h-7 w-7 rounded-md text-muted-foreground hover:bg-accent/35 hover:text-foreground",
            hideSidebarToggleOnDesktop && "lg:pointer-events-none lg:opacity-0",
          )}
        >
          <Menu className="h-3.5 w-3.5" />
        </Button>
        <ThemeButton theme={theme} onToggleTheme={onToggleTheme} label={t("thread.header.toggleTheme")} />
      </div>
    );
  }

  return (
    <div className="relative z-10 flex items-center justify-between gap-3 px-3 py-2">
      <div className="relative flex min-w-0 items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("thread.header.toggleSidebar")}
          onClick={onToggleSidebar}
          className={cn(
            "h-7 w-7 rounded-md text-muted-foreground hover:bg-accent/35 hover:text-foreground",
            hideSidebarToggleOnDesktop && "lg:pointer-events-none lg:opacity-0",
          )}
        >
          <Menu className="h-3.5 w-3.5" />
        </Button>
        <div className="flex min-w-0 items-center rounded-md px-1.5 py-1 text-[12px] font-medium text-muted-foreground">
          <span className="max-w-[min(60vw,32rem)] truncate">{title}</span>
        </div>
        {activeGoal ? (
          <div
            className="hidden min-w-0 max-w-[min(42vw,28rem)] items-center gap-1.5 rounded-md border border-border/70 bg-background/70 px-2 py-1 text-[11px] font-medium text-foreground/75 shadow-sm sm:flex"
            title={activeGoal}
            aria-label={t("thread.header.activeGoal", { defaultValue: "Active goal" })}
          >
            {runStartedAt != null ? (
              <LoaderCircle className="h-3.5 w-3.5 shrink-0 animate-spin text-[#4f9de8]" />
            ) : (
              <Target className="h-3.5 w-3.5 shrink-0 text-[#53c59d]" />
            )}
            <span className="truncate">{activeGoal}</span>
          </div>
        ) : null}
      </div>

      <ThemeButton theme={theme} onToggleTheme={onToggleTheme} label={t("thread.header.toggleTheme")} />

      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-full h-4" />
    </div>
  );
}

function ThemeButton({
  theme,
  onToggleTheme,
  label,
}: {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  label: string;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      onClick={onToggleTheme}
      className="h-8 w-8 rounded-full text-muted-foreground/85 hover:bg-accent/40 hover:text-foreground"
    >
      {theme === "dark" ? (
        <Sun className="h-4 w-4" />
      ) : (
        <Moon className="h-4 w-4" />
      )}
    </Button>
  );
}

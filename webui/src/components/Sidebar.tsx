import { type ReactNode } from "react";
import {
  Activity,
  ListChecks,
  Menu,
  Settings,
  Signal,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConnectionBadge } from "@/components/ConnectionBadge";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

interface SidebarProps {
  onOpenSettings: () => void;
  onOpenReviews: () => void;
  onOpenCognition: () => void;
  onOpenSignals: () => void;
  onCollapse: () => void;
}

function SidebarActionButton({
  children,
  icon,
  onClick,
}: {
  children: ReactNode;
  icon: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group relative flex h-9 w-full items-center justify-center rounded-full border-2",
        "px-4 text-[12px] font-black leading-none tracking-normal transition-all duration-200",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#19c8b9] focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar",
        "border-[#aaa69d] bg-[#f8f8f0] text-[#794f27] shadow-[0_2px_4px_rgba(61,52,40,0.06)] hover:-translate-y-px hover:border-[#19c8b9] hover:text-[#19c8b9] hover:shadow-[0_3px_10px_rgba(61,52,40,0.10)] active:translate-y-0 active:border-[#50b9ab] active:text-[#50b9ab]",
      )}
    >
      <span className="grid min-w-[7.25rem] grid-cols-[1rem_auto_1rem] items-center justify-center gap-2">
        <span className="flex h-4 w-4 items-center justify-center">{icon}</span>
        <span className="text-center">{children}</span>
        <span aria-hidden className="h-4 w-4" />
      </span>
    </button>
  );
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation();

  return (
    <nav
      aria-label={t("sidebar.navigation")}
      className="island-sidebar-nav flex h-full w-full min-w-0 flex-col border-r border-sidebar-border/60 bg-sidebar text-sidebar-foreground"
    >
      <div className="flex min-h-[56px] items-center justify-between gap-2 px-3 pb-3 pt-3.5">
        <picture className="block min-w-0 flex-1">
          <img
            src="/brand/OriginAgent_logo_v2.svg"
            alt="OriginAgent"
            className="h-11 max-w-[196px] select-none object-contain object-left opacity-95"
            draggable={false}
          />
        </picture>
        <button
          type="button"
          aria-label={t("sidebar.collapse")}
          onClick={props.onCollapse}
          className="island-icon-button"
        >
          <Menu className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex min-h-0 min-w-0 flex-1" />

      <Separator className="bg-sidebar-border/50" />
      <div className="space-y-1 px-2.5 py-2.5 text-xs">
        <SidebarActionButton
          onClick={props.onOpenCognition}
          icon={<Activity className="h-3.5 w-3.5" aria-hidden />}
        >
          {t("sidebar.cognition")}
        </SidebarActionButton>
        <SidebarActionButton
          onClick={props.onOpenSignals}
          icon={<Signal className="h-3.5 w-3.5" aria-hidden />}
        >
          {t("sidebar.signals")}
        </SidebarActionButton>
        <SidebarActionButton
          onClick={props.onOpenReviews}
          icon={<ListChecks className="h-3.5 w-3.5" aria-hidden />}
        >
          {t("sidebar.reviews")}
        </SidebarActionButton>
        <SidebarActionButton
          onClick={props.onOpenSettings}
          icon={<Settings className="h-3.5 w-3.5" aria-hidden />}
        >
          {t("sidebar.settings")}
        </SidebarActionButton>
        <ConnectionBadge />
      </div>
    </nav>
  );
}

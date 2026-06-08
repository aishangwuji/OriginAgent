import { ChevronDown, Download, Loader2, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { FetchedProviderModel, ProviderModelsResponse } from "@/lib/types";

type ModelInputWithFetchProps = {
  value: string;
  onChange: (value: string) => void;
  onFetch?: () => void;
  onRefresh?: () => void;
  fetchedModels: FetchedProviderModel[];
  fetchPayload?: ProviderModelsResponse | null;
  hasFetched: boolean;
  isLoading: boolean;
  placeholder?: string;
  providerLabel?: string;
  providerCatalogKind?: "official" | "catalog" | "local" | "custom" | "unsupported";
  errorMessage?: string | null;
};

function groupModels(models: FetchedProviderModel[]) {
  const grouped = new Map<string, FetchedProviderModel[]>();
  for (const model of models) {
    const key = model.owned_by?.trim() || "Other";
    const current = grouped.get(key) ?? [];
    current.push(model);
    grouped.set(key, current);
  }
  return [...grouped.entries()].map(([label, rows]) => [
    label,
    [...rows].sort((left, right) => left.id.localeCompare(right.id)),
  ] as const);
}

export function ModelInputWithFetch({
  value,
  onChange,
  onFetch,
  onRefresh,
  fetchedModels,
  fetchPayload,
  hasFetched,
  isLoading,
  placeholder,
  providerLabel,
  providerCatalogKind = "unsupported",
  errorMessage,
}: ModelInputWithFetchProps) {
  const { t } = useTranslation();
  const normalizedValue = value.trim().toLowerCase();
  const fetchedAtLabel =
    fetchPayload?.fetched_at != null
      ? new Intl.DateTimeFormat(undefined, {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(new Date(fetchPayload.fetched_at * 1000))
      : null;
  const isCatalogProvider = providerCatalogKind === "catalog";
  const isCatalogReady = !isCatalogProvider || normalizedValue.length >= 2;
  const visibleModels = isCatalogProvider
    ? fetchedModels.filter((model) => model.id.toLowerCase().includes(normalizedValue))
    : fetchedModels;
  const grouped = isCatalogReady ? groupModels(visibleModels) : [];
  const canRefresh = !!fetchPayload && !!onRefresh;
  const hasModels = grouped.length > 0;
  const dropdownDisabled = !hasModels;
  const fetchButtonLabel = canRefresh ? t("settings.modelFetch.refreshModels") : t("settings.modelFetch.fetchModels");
  const fetchButtonLoadingLabel = t("settings.modelFetch.fetchingModels");
  const fetchButtonDisabled = isLoading || !isCatalogReady;

  let helperMessage: string | null = null;
  if (errorMessage) {
    helperMessage = errorMessage;
  } else if (providerCatalogKind === "local") {
    helperMessage = t("settings.modelFetch.fetchModelsLocal");
  } else if (isCatalogProvider && !isCatalogReady) {
    helperMessage = t("settings.modelFetch.searchToLoadCatalog", {
      provider: providerLabel ?? "",
      minChars: 2,
    });
  } else if (!fetchPayload && !hasFetched) {
    if (providerCatalogKind === "custom" || providerCatalogKind === "unsupported") {
      helperMessage = t("settings.modelFetch.fetchModelsUnsupported");
    } else {
      helperMessage = t("settings.modelFetch.openListHint");
    }
  } else if (isCatalogProvider && fetchPayload && grouped.length === 0) {
    helperMessage = t("settings.modelFetch.catalogNoMatches");
  } else if (hasFetched && grouped.length === 0) {
    helperMessage = t("settings.modelFetch.noFetchedModels");
  } else if (providerCatalogKind === "custom" || providerCatalogKind === "unsupported") {
    helperMessage = t("settings.modelFetch.manualInputStillAllowed");
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className="h-8 w-[280px] rounded-full text-[13px]"
        />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="h-8 w-8 rounded-full"
              aria-label={t("settings.modelFetch.openList")}
              title={dropdownDisabled ? t("settings.modelFetch.openListHint") : t("settings.modelFetch.openList")}
              disabled={dropdownDisabled}
            >
              <ChevronDown className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-h-72 w-72 overflow-y-auto">
            {grouped.map(([group, rows], index) => (
              <div key={group}>
                {index > 0 ? <DropdownMenuSeparator /> : null}
                <DropdownMenuLabel>{group}</DropdownMenuLabel>
                {rows.map((model) => (
                  <DropdownMenuItem key={model.id} onSelect={() => onChange(model.id)}>
                    {model.id}
                  </DropdownMenuItem>
                ))}
              </div>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        {!canRefresh ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 rounded-full px-3 text-[12px]"
            onClick={onFetch}
            disabled={fetchButtonDisabled}
            aria-label={isLoading ? fetchButtonLoadingLabel : fetchButtonLabel}
            title={isLoading ? fetchButtonLoadingLabel : fetchButtonLabel}
          >
            {isLoading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Download className="mr-1 h-4 w-4" />}
            {isLoading ? fetchButtonLoadingLabel : fetchButtonLabel}
          </Button>
        ) : null}
        {canRefresh ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 rounded-full px-3 text-[12px]"
            onClick={onRefresh}
            disabled={isLoading}
            aria-label={isLoading ? fetchButtonLoadingLabel : fetchButtonLabel}
            title={isLoading ? fetchButtonLoadingLabel : fetchButtonLabel}
          >
            {isLoading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RotateCcw className="mr-1 h-4 w-4" />}
            {isLoading ? fetchButtonLoadingLabel : fetchButtonLabel}
          </Button>
        ) : null}
      </div>
      {fetchPayload ? (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-muted-foreground">
          <span>
            {fetchPayload.cached
              ? t("settings.modelFetch.cachedModels")
              : t("settings.modelFetch.fetchedModelCount", { count: fetchPayload.model_count })}
          </span>
          {fetchedAtLabel ? (
            <span>{t("settings.modelFetch.lastFetchedAt", { time: fetchedAtLabel })}</span>
          ) : null}
        </div>
      ) : null}
      {helperMessage ? (
        <p className="text-[12px] text-muted-foreground">{helperMessage}</p>
      ) : null}
    </div>
  );
}

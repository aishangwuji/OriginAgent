import { ChevronDown, Download, Loader2 } from "lucide-react";
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
import type { FetchedProviderModel } from "@/lib/types";

type ModelInputWithFetchProps = {
  value: string;
  onChange: (value: string) => void;
  onFetch: () => void;
  fetchedModels: FetchedProviderModel[];
  hasFetched: boolean;
  isLoading: boolean;
  placeholder?: string;
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
  fetchedModels,
  hasFetched,
  isLoading,
  placeholder,
}: ModelInputWithFetchProps) {
  const { t } = useTranslation();
  const grouped = groupModels(fetchedModels);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className="h-8 w-[280px] rounded-full text-[13px]"
        />
        {grouped.length > 0 ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="h-8 w-8 rounded-full"
                aria-label={t("settings.modelFetch.openList")}
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
        ) : null}
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="h-8 w-8 rounded-full"
          onClick={onFetch}
          disabled={isLoading}
          aria-label={isLoading ? t("settings.modelFetch.fetchingModels") : t("settings.modelFetch.fetchModels")}
          title={isLoading ? t("settings.modelFetch.fetchingModels") : t("settings.modelFetch.fetchModels")}
        >
          {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
        </Button>
      </div>
      {hasFetched && grouped.length === 0 ? (
        <p className="text-[12px] text-muted-foreground">{t("settings.modelFetch.noFetchedModels")}</p>
      ) : null}
    </div>
  );
}

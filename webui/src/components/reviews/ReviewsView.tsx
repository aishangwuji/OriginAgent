import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Check,
  Clock3,
  FileText,
  ListChecks,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchReviewProposal,
  listReviewProposals,
  reviewProposalAction,
  withTokenRefresh,
} from "@/lib/api";
import type {
  ReviewDecisionResult,
  ReviewProposal,
  ReviewProposalStats,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

type ReviewStatusFilter = "pending" | "applied" | "rejected" | "deferred" | "failed" | "";
type ReviewTypeFilter = "memory" | "fact" | "skill" | "workflow" | "";

const STATUS_FILTERS: ReviewStatusFilter[] = ["pending", "applied", "rejected", "deferred", "failed", ""];
const TYPE_FILTERS: ReviewTypeFilter[] = ["", "memory", "fact", "skill", "workflow"];
const APPLICABLE_TYPES = new Set(["memory", "fact", "skill", "workflow"]);

interface ReviewsViewProps {
  onBackToChat: () => void;
}

function normalizeType(proposal: ReviewProposal | null): string {
  return String(proposal?.proposal_type ?? "").toLowerCase();
}

function isSupportedType(proposal: ReviewProposal | null): boolean {
  return APPLICABLE_TYPES.has(normalizeType(proposal));
}

function statusIcon(status: string) {
  switch (status) {
    case "applied":
      return <ShieldCheck className="h-3.5 w-3.5" aria-hidden />;
    case "rejected":
      return <X className="h-3.5 w-3.5" aria-hidden />;
    case "deferred":
      return <Clock3 className="h-3.5 w-3.5" aria-hidden />;
    case "failed":
      return <AlertCircle className="h-3.5 w-3.5" aria-hidden />;
    default:
      return <ListChecks className="h-3.5 w-3.5" aria-hidden />;
  }
}

function confidenceLabel(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "";
  return `${Math.round(value * 100)}%`;
}

export function ReviewsView({ onBackToChat }: ReviewsViewProps) {
  const { t } = useTranslation();
  const { token, refreshToken } = useClient();
  const [statusFilter, setStatusFilter] = useState<ReviewStatusFilter>("pending");
  const [typeFilter, setTypeFilter] = useState<ReviewTypeFilter>("");
  const [proposals, setProposals] = useState<ReviewProposal[]>([]);
  const [stats, setStats] = useState<ReviewProposalStats | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReviewProposal | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actioning, setActioning] = useState<string | null>(null);
  const [confirmApply, setConfirmApply] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
        listReviewProposals(freshToken, {
          status: statusFilter || undefined,
          type: typeFilter || undefined,
          limit: 50,
        }),
      );
      setProposals(payload.proposals);
      setStats(payload.stats);
      setSelectedId((current) => {
        if (current && payload.proposals.some((proposal) => proposal.id === current)) {
          return current;
        }
        return payload.proposals[0]?.id ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [refreshToken, statusFilter, token, typeFilter]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    withTokenRefresh(token, refreshToken, (freshToken) =>
      fetchReviewProposal(freshToken, selectedId),
    )
      .then((payload) => {
        if (cancelled) return;
        setSelected(payload.proposal);
        setStats(payload.stats);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshToken, selectedId, token]);

  const selectedStatus = selected?.status ?? "pending";
  const terminal = ["applied", "rejected", "deferred", "failed"].includes(String(selectedStatus));
  const canApplySelected =
    selected && typeof selected.can_apply === "boolean"
      ? selected.can_apply
      : Boolean(selected && !terminal && isSupportedType(selected));
  const applyDisabled = !selected || terminal || !canApplySelected;
  const selectedArtifact = selected?.apply_artifact ?? selected?.review_event?.artifact;
  const selectedPayloadSkillName =
    typeof selected?.payload?.skill_name === "string" ? selected.payload.skill_name : "";
  const selectedPayloadWorkflowName =
    typeof selected?.payload?.workflow_name === "string" ? selected.payload.workflow_name : "";

  const runAction = useCallback(
    async (action: "apply" | "reject" | "defer") => {
      if (!selected) return;
      setActioning(action);
      setNotice(null);
      setError(null);
      try {
        const payload = await withTokenRefresh(token, refreshToken, (freshToken) =>
          reviewProposalAction(freshToken, selected.id, action, reason),
        );
        setSelected(payload.proposal);
        setStats(payload.stats);
        const result: ReviewDecisionResult = payload.result;
        setNotice(result.message || t("reviews.status.saved"));
        setReason("");
        await loadList();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setActioning(null);
        setConfirmApply(false);
      }
    },
    [loadList, reason, refreshToken, selected, t, token],
  );

  const headerStats = useMemo(() => {
    if (!stats) return null;
    return [
      t("reviews.stats.total", { count: stats.proposal_count }),
      t("reviews.stats.pending", { count: stats.pending_count }),
    ].join(" · ");
  }, [stats, t]);

  return (
    <section className="flex h-full min-h-0 flex-col bg-card/40">
      <header className="flex min-h-[64px] items-center justify-between gap-3 border-b border-border/60 px-4 py-3 sm:px-5">
        <div className="flex min-w-0 items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="rounded-full"
            onClick={onBackToChat}
            aria-label={t("reviews.backToChat")}
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
          </Button>
          <div className="min-w-0">
            <h1 className="truncate text-lg font-black leading-tight text-foreground">
              {t("reviews.title")}
            </h1>
            {headerStats ? (
              <p className="mt-1 truncate text-xs text-muted-foreground">{headerStats}</p>
            ) : null}
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void loadList()}
          disabled={loading}
          className="rounded-full"
        >
          <RefreshCw className={cn("mr-2 h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
          {t("reviews.actions.refresh")}
        </Button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[21rem_minmax(0,1fr)]">
        <aside className="flex min-h-[15rem] min-w-0 flex-col border-b border-border/60 lg:border-b-0 lg:border-r">
          <div className="space-y-3 border-b border-border/60 p-3">
            <div className="flex flex-wrap gap-2" aria-label={t("reviews.filters.status")}>
              {STATUS_FILTERS.map((status) => (
                <button
                  key={status || "all"}
                  type="button"
                  onClick={() => setStatusFilter(status)}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs font-black transition",
                    statusFilter === status
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border/70 bg-background/60 text-muted-foreground hover:border-primary/70 hover:text-foreground",
                  )}
                >
                  {t(status ? `reviews.status.${status}` : "reviews.filters.all")}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-2" aria-label={t("reviews.filters.type")}>
              {TYPE_FILTERS.map((type) => (
                <button
                  key={type || "all"}
                  type="button"
                  onClick={() => setTypeFilter(type)}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs font-black transition",
                    typeFilter === type
                      ? "border-accent bg-accent text-accent-foreground"
                      : "border-border/70 bg-background/60 text-muted-foreground hover:border-accent/70 hover:text-foreground",
                  )}
                >
                  {t(type ? `reviews.types.${type}` : "reviews.filters.allTypes")}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {loading ? (
              <p className="px-2 py-8 text-sm text-muted-foreground">{t("reviews.loading")}</p>
            ) : proposals.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border/70 bg-background/45 p-4 text-sm text-muted-foreground">
                {t("reviews.empty")}
              </div>
            ) : (
              <div className="space-y-2">
                {proposals.map((proposal) => {
                  const active = proposal.id === selectedId;
                  const confidence = confidenceLabel(proposal.confidence);
                  return (
                    <button
                      key={proposal.id}
                      type="button"
                      onClick={() => setSelectedId(proposal.id)}
                      className={cn(
                        "w-full rounded-lg border p-3 text-left transition",
                        active
                          ? "border-primary bg-primary/10 shadow-sm"
                          : "border-border/70 bg-background/55 hover:border-primary/50 hover:bg-background/80",
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="inline-flex min-w-0 items-center gap-1.5 text-[11px] font-black uppercase text-muted-foreground">
                          {statusIcon(String(proposal.status || "pending"))}
                          {t(`reviews.status.${proposal.status || "pending"}`)}
                        </span>
                        <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-black text-muted-foreground">
                          {proposal.proposal_type}
                        </span>
                      </div>
                      <p className="mt-2 line-clamp-2 text-sm font-black text-foreground">
                        {proposal.title || t("reviews.untitled")}
                      </p>
                      <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
                        {proposal.content}
                      </p>
                      {confidence ? (
                        <p className="mt-2 text-[11px] text-muted-foreground">
                          {t("reviews.confidence", { value: confidence })}
                        </p>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        <main className="min-h-0 overflow-y-auto p-4 sm:p-6">
          {error ? (
            <div role="alert" className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          ) : null}
          {notice ? (
            <div role="status" className="mb-4 rounded-lg border border-primary/30 bg-primary/10 p-3 text-sm font-semibold text-foreground">
              {notice}
            </div>
          ) : null}

          {!selected ? (
            <div className="flex h-full min-h-[20rem] items-center justify-center rounded-lg border border-dashed border-border/70 bg-background/40 p-8 text-center text-sm text-muted-foreground">
              {detailLoading ? t("reviews.loading") : t("reviews.noSelection")}
            </div>
          ) : (
            <article className="mx-auto flex max-w-4xl flex-col gap-4">
              <div className="rounded-lg border border-border/70 bg-background/70 p-4 shadow-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs font-black">
                    {statusIcon(String(selected.status || "pending"))}
                    {t(`reviews.status.${selected.status || "pending"}`)}
                  </span>
                  <span className="rounded-full bg-secondary px-3 py-1 text-xs font-black text-secondary-foreground">
                    {selected.proposal_type}/{selected.domain_id || "core"}
                  </span>
                  {selected.applied_fact_id ? (
                    <span className="rounded-full bg-primary/15 px-3 py-1 text-xs font-black text-foreground">
                      {t("reviews.appliedFact", { id: selected.applied_fact_id })}
                    </span>
                  ) : null}
                  {selected.applied_skill_path ? (
                    <span className="rounded-full bg-primary/15 px-3 py-1 text-xs font-black text-foreground">
                      {t("reviews.appliedSkill", { path: selected.applied_skill_path })}
                    </span>
                  ) : null}
                  {selected.applied_workflow_path ? (
                    <span className="rounded-full bg-primary/15 px-3 py-1 text-xs font-black text-foreground">
                      {t("reviews.appliedWorkflow", { path: selected.applied_workflow_path })}
                    </span>
                  ) : null}
                </div>
                <h2 className="mt-4 text-xl font-black leading-tight text-foreground">
                  {selected.title || t("reviews.untitled")}
                </h2>
                <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-foreground">
                  {selected.content}
                </p>
              </div>

              <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
                <div className="rounded-lg border border-border/70 bg-background/65 p-4">
                  <h3 className="flex items-center gap-2 text-sm font-black">
                    <FileText className="h-4 w-4" aria-hidden />
                    {t("reviews.sections.context")}
                  </h3>
                  <dl className="mt-3 grid gap-3 text-sm">
                    <div>
                      <dt className="text-xs font-black uppercase text-muted-foreground">{t("reviews.fields.session")}</dt>
                      <dd className="mt-1 break-all">{selected.session_key || "-"}</dd>
                    </div>
                    <div>
                      <dt className="text-xs font-black uppercase text-muted-foreground">{t("reviews.fields.created")}</dt>
                      <dd className="mt-1">{selected.created_at || "-"}</dd>
                    </div>
                    <div>
                      <dt className="text-xs font-black uppercase text-muted-foreground">{t("reviews.fields.rationale")}</dt>
                      <dd className="mt-1 whitespace-pre-wrap">{selected.rationale || "-"}</dd>
                    </div>
                    {selected.review_reason ? (
                      <div>
                        <dt className="text-xs font-black uppercase text-muted-foreground">{t("reviews.fields.reviewReason")}</dt>
                        <dd className="mt-1 whitespace-pre-wrap">{selected.review_reason}</dd>
                      </div>
                    ) : null}
                  </dl>
                </div>

                <div className="rounded-lg border border-border/70 bg-background/65 p-4">
                  <h3 className="text-sm font-black">{t("reviews.sections.evidence")}</h3>
                  {selected.evidence?.length ? (
                    <ul className="mt-3 space-y-2 text-xs leading-5 text-muted-foreground">
                      {selected.evidence.map((item, index) => (
                        <li key={`${index}-${item}`} className="rounded-md bg-muted/50 p-2">
                          {item}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-3 text-xs text-muted-foreground">{t("reviews.noEvidence")}</p>
                  )}
                </div>
              </div>

              <div className="rounded-lg border border-border/70 bg-background/70 p-4">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
                  <label className="min-w-0 flex-1">
                    <span className="text-xs font-black uppercase text-muted-foreground">
                      {t("reviews.reasonLabel")}
                    </span>
                    <Textarea
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                      placeholder={t("reviews.reasonPlaceholder")}
                      className="mt-2 min-h-[5rem] resize-none rounded-lg bg-card/70"
                    />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      onClick={() => setConfirmApply(true)}
                      disabled={applyDisabled || actioning !== null}
                      className="rounded-full"
                    >
                      <Check className="mr-2 h-4 w-4" aria-hidden />
                      {t("reviews.actions.apply")}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void runAction("defer")}
                      disabled={!selected || terminal || actioning !== null}
                      className="rounded-full"
                    >
                      <Clock3 className="mr-2 h-4 w-4" aria-hidden />
                      {t("reviews.actions.defer")}
                    </Button>
                    <Button
                      type="button"
                      variant="destructive"
                      onClick={() => void runAction("reject")}
                      disabled={!selected || terminal || actioning !== null}
                      className="rounded-full"
                    >
                      <X className="mr-2 h-4 w-4" aria-hidden />
                      {t("reviews.actions.reject")}
                    </Button>
                  </div>
                </div>
                {!isSupportedType(selected) ? (
                  <p className="mt-3 text-xs text-muted-foreground">
                    {selected.unsupported_reason || t("reviews.unsupportedApply")}
                  </p>
                ) : null}
              </div>
            </article>
          )}
        </main>
      </div>

      <AlertDialog open={confirmApply} onOpenChange={setConfirmApply}>
        <AlertDialogContent className="rounded-[24px]">
          <AlertDialogHeader>
            <AlertDialogTitle>{t("reviews.confirmApply.title")}</AlertDialogTitle>
            <AlertDialogDescription>
              {normalizeType(selected) === "skill"
                ? t("reviews.confirmApply.skillDescription", {
                    name: selectedArtifact?.skill_name || selectedPayloadSkillName || selected?.title || "",
                    path: selectedArtifact?.path || t("reviews.confirmApply.skillPathPending"),
                  })
                : normalizeType(selected) === "workflow"
                  ? t("reviews.confirmApply.workflowDescription", {
                      name:
                        selectedArtifact?.workflow_name ||
                        selectedPayloadWorkflowName ||
                        selected?.title ||
                        "",
                      path: selectedArtifact?.path || t("reviews.confirmApply.workflowPathPending"),
                    })
                : t("reviews.confirmApply.description")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("reviews.confirmApply.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => void runAction("apply")}>
              {t("reviews.confirmApply.confirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

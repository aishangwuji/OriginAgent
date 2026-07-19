import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, RefreshCw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  ApiError,
  approveApproval,
  fetchTenants,
  listApprovals,
  rejectApproval,
  userFriendlyError,
  type Approval,
} from "@/lib/api";

interface ApprovalsPanelProps {
  token: string;
  baseUrl: string;
}

const POLL_INTERVAL_MS = 30_000;
const PROMPT_TRUNCATE_LEN = 80;

type LoadState = "loading" | "ready" | "error";

/**
 * ApprovalsPanel — lists pending tool approvals for the current owner and
 * provides Approve / Reject actions.
 *
 * 规则 26 假设显式化（owner_id 来源）：
 * The WebUI auth model is a single shared token (no per-user role context
 * such as useAuth().role). The owner's tenant_id is resolved via
 * ``fetchTenants().defaultTenantId`` — the same source TenantsSettings uses.
 * The panel is visible to any logged-in WebUI user; cross-owner approval
 * isolation is enforced by the backend (D10 owner_id check, returns 403).
 *
 * 规则 5 (cache lifecycle): ``ownerId`` is cached in state for the panel's
 * lifetime. If the owner changes their own tenant_id via TenantsSettings,
 * the user must reopen ApprovalsPanel to pick up the new id. This is an
 * acceptable trade-off because changing the default tenant is a rare
 * operation that requires a settings save + restart.
 *
 * 规则 8 (async timing): polling continues at 30s interval. If a poll
 * response arrives after an approve/reject action but before the next poll,
 * the item may briefly reappear — it will be corrected on the next poll
 * because the backend has already resolved the confirmation.
 *
 * 规则 18 (security boundary): owner_id is sourced from a trusted API
 * response (fetchTenants), never from URL params or user input. Cross-owner
 * approval is rejected by the backend with 403.
 */
export function ApprovalsPanel({ token, baseUrl }: ApprovalsPanelProps) {
  const { t } = useTranslation();
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [ownerId, setOwnerId] = useState<string | null>(null);
  const [ownerError, setOwnerError] = useState<string | null>(null);
  const [submittingId, setSubmittingId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // Resolve owner's tenant_id once on mount. See rule 5 note above.
  useEffect(() => {
    let cancelled = false;
    fetchTenants(token, baseUrl)
      .then((payload) => {
        if (!cancelled) setOwnerId(payload.defaultTenantId);
      })
      .catch((err) => {
        if (!cancelled) setOwnerError(userFriendlyError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [token, baseUrl]);

  const loadApprovals = useCallback(
    async (isInitial: boolean) => {
      if (!ownerId) return;
      // Only show the loading spinner on the very first fetch; subsequent
      // polls update the list silently to avoid flicker.
      if (isInitial) setLoadState("loading");
      setErrorMessage(null);
      try {
        const list = await listApprovals(token, ownerId, baseUrl);
        setApprovals(list);
        setLoadState("ready");
      } catch (err) {
        setErrorMessage(mapApprovalError(err, t));
        setLoadState("error");
      }
    },
    [ownerId, token, baseUrl, t],
  );

  // Initial fetch + 30s polling. Re-runs only when ownerId/token/baseUrl
  // change (i.e. once when ownerId resolves).
  useEffect(() => {
    if (!ownerId) return;
    let cancelled = false;
    const poll = () => {
      if (cancelled) return;
      void loadApprovals(false);
    };
    void loadApprovals(true);
    const intervalId = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [loadApprovals, ownerId]);

  const handleApprove = useCallback(
    async (confirmationId: string) => {
      if (!ownerId) return;
      if (!window.confirm(t("approvals.confirm_approve"))) return;
      setSubmittingId(confirmationId);
      setErrorMessage(null);
      try {
        await approveApproval(token, confirmationId, ownerId, baseUrl);
        setApprovals((prev) =>
          prev.filter((a) => a.confirmation_id !== confirmationId),
        );
        setStatusMessage(t("approvals.success.approved"));
      } catch (err) {
        setErrorMessage(mapApprovalError(err, t));
      } finally {
        setSubmittingId(null);
      }
    },
    [ownerId, token, baseUrl, t],
  );

  const handleReject = useCallback(
    async (confirmationId: string) => {
      if (!ownerId) return;
      if (!window.confirm(t("approvals.confirm_reject"))) return;
      setSubmittingId(confirmationId);
      setErrorMessage(null);
      try {
        await rejectApproval(token, confirmationId, ownerId, baseUrl);
        setApprovals((prev) =>
          prev.filter((a) => a.confirmation_id !== confirmationId),
        );
        setStatusMessage(t("approvals.success.rejected"));
      } catch (err) {
        setErrorMessage(mapApprovalError(err, t));
      } finally {
        setSubmittingId(null);
      }
    },
    [ownerId, token, baseUrl, t],
  );

  // Auto-clear success message after 3s (lightweight toast替代).
  useEffect(() => {
    if (!statusMessage) return;
    const id = setTimeout(() => setStatusMessage(null), 3000);
    return () => clearTimeout(id);
  }, [statusMessage]);

  // Owner-id fetch failed — show error early (cannot proceed without it).
  if (ownerError) {
    return (
      <div className="space-y-5">
        <SectionHeader />
        <div className="rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
          {ownerError}
        </div>
      </div>
    );
  }

  // Still resolving owner_id — show a spinner without text (brief transient).
  if (!ownerId) {
    return (
      <div className="space-y-5">
        <SectionHeader />
        <div className="flex h-32 items-center justify-center rounded-[18px] border border-border/50 bg-card/70 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        </div>
      </div>
    );
  }

  const showSpinner = loadState === "loading" && approvals.length === 0;
  const showError = loadState === "error" && approvals.length === 0;

  return (
    <div className="space-y-5">
      <SectionHeader
        onRefresh={() => void loadApprovals(false)}
        refreshing={loadState === "loading"}
      />

      {statusMessage ? (
        <div className="rounded-[18px] border border-emerald-500/30 bg-emerald-500/5 px-4 py-2.5 text-[13px] text-emerald-700 dark:text-emerald-300">
          {statusMessage}
        </div>
      ) : null}

      {errorMessage && !showError ? (
        <div className="rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
          {errorMessage}
        </div>
      ) : null}

      {showSpinner ? (
        <div className="flex h-32 items-center justify-center rounded-[18px] border border-border/50 bg-card/70 text-[13px] text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        </div>
      ) : showError ? (
        <div className="rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
          {errorMessage ?? t("approvals.error.network")}
        </div>
      ) : approvals.length === 0 ? (
        <div className="rounded-[18px] border border-dashed border-border/60 bg-card/45 px-4 py-5 text-[13px] text-muted-foreground">
          {t("approvals.empty")}
        </div>
      ) : (
        <div className="overflow-hidden rounded-[18px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="bg-muted/40 text-[11px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-4 py-2.5 font-medium">ID</th>
                  <th className="px-4 py-2.5 font-medium">{t("approvals.tool")}</th>
                  <th className="px-4 py-2.5 font-medium">Prompt</th>
                  <th className="px-4 py-2.5 font-medium">{t("approvals.created_at")}</th>
                  <th className="px-4 py-2.5 font-medium">{t("approvals.expires_at")}</th>
                  <th className="px-4 py-2.5 font-medium">Risk</th>
                  <th className="px-4 py-2.5" aria-label="actions" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border/45">
                {approvals.map((approval) => (
                  <ApprovalRow
                    key={approval.confirmation_id}
                    approval={approval}
                    onApprove={() => void handleApprove(approval.confirmation_id)}
                    onReject={() => void handleReject(approval.confirmation_id)}
                    submitting={submittingId === approval.confirmation_id}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function SectionHeader({
  onRefresh,
  refreshing,
}: {
  onRefresh?: () => void;
  refreshing?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-foreground">
          {t("approvals.title")}
        </h2>
      </div>
      {onRefresh ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="rounded-full"
          onClick={onRefresh}
          disabled={refreshing}
        >
          <RefreshCw
            className={refreshing ? "mr-1 h-3.5 w-3.5 animate-spin" : "mr-1 h-3.5 w-3.5"}
            aria-hidden
          />
          {t("approvals.refresh")}
        </Button>
      ) : null}
    </div>
  );
}

function ApprovalRow({
  approval,
  onApprove,
  onReject,
  submitting,
}: {
  approval: Approval;
  onApprove: () => void;
  onReject: () => void;
  submitting: boolean;
}) {
  const { t } = useTranslation();
  const toolName = approval.metadata?.tool_name ?? approval.action ?? "—";
  const promptText =
    approval.prompt.length > PROMPT_TRUNCATE_LEN
      ? `${approval.prompt.slice(0, PROMPT_TRUNCATE_LEN)}…`
      : approval.prompt;

  return (
    <tr className="align-top">
      <td className="px-4 py-3 font-mono text-[12px] text-foreground/85">
        {approval.confirmation_id}
      </td>
      <td className="px-4 py-3 text-foreground">{toolName}</td>
      <td className="px-4 py-3 text-muted-foreground">{promptText}</td>
      <td className="px-4 py-3 text-muted-foreground">{formatDateTime(approval.created_at)}</td>
      <td className="px-4 py-3 text-muted-foreground">
        <div>{formatDateTime(approval.expires_at)}</div>
        {renderRemaining(approval.expires_at) ? (
          <div className="text-[11px] text-muted-foreground/70">
            {renderRemaining(approval.expires_at)}
          </div>
        ) : null}
      </td>
      <td className="px-4 py-3">
        <RiskBadge risk={approval.risk} />
      </td>
      <td className="px-4 py-3">
        <div className="flex justify-end gap-1.5">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-[12px] text-emerald-700 hover:text-emerald-700 dark:text-emerald-300"
            onClick={onApprove}
            disabled={submitting}
          >
            <Check className="mr-1 h-3 w-3" aria-hidden />
            {t("approvals.approve")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-2 text-[12px] text-destructive hover:text-destructive"
            onClick={onReject}
            disabled={submitting}
          >
            <X className="mr-1 h-3 w-3" aria-hidden />
            {t("approvals.reject")}
          </Button>
        </div>
      </td>
    </tr>
  );
}

function RiskBadge({ risk }: { risk: string | null }) {
  const { t } = useTranslation();
  const normalized = (risk ?? "").toLowerCase();
  if (normalized === "high") {
    return (
      <span className="inline-flex w-fit items-center rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-medium text-red-700 dark:text-red-300">
        {t("approvals.risk.high")}
      </span>
    );
  }
  if (normalized === "medium") {
    return (
      <span className="inline-flex w-fit items-center rounded-full bg-orange-500/15 px-2 py-0.5 text-[11px] font-medium text-orange-700 dark:text-orange-300">
        {t("approvals.risk.medium")}
      </span>
    );
  }
  if (normalized === "low") {
    return (
      <span className="inline-flex w-fit items-center rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
        {t("approvals.risk.low")}
      </span>
    );
  }
  return <span className="text-[11px] text-muted-foreground">—</span>;
}

function formatDateTime(iso: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function renderRemaining(expiresAt: string): string {
  if (!expiresAt) return "";
  try {
    const d = new Date(expiresAt);
    if (Number.isNaN(d.getTime())) return "";
    const diffMs = d.getTime() - Date.now();
    if (diffMs <= 0) return "";
    const minutes = Math.floor(diffMs / 60_000);
    const seconds = Math.floor((diffMs % 60_000) / 1000);
    return `in ${minutes}m ${seconds}s`;
  } catch {
    return "";
  }
}

/**
 * Maps an API error to a user-facing message using the approvals.* i18n keys
 * for known cases (403 permission denied, 404 not found, network error),
 * falling back to the shared ``userFriendlyError`` helper for other statuses.
 */
function mapApprovalError(err: unknown, t: (key: string) => string): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return t("approvals.permission_denied");
    if (err.status === 404) return t("approvals.error.not_found");
  }
  // Network errors typically surface as TypeError ("Failed to fetch") —
  // ApiError wrapping happens in request<T>, but a totally unreachable
  // host surfaces as a raw TypeError before that.
  if (err instanceof TypeError) return t("approvals.error.network");
  return userFriendlyError(err);
}

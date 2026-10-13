import { type ReactNode, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Info, Loader2, Pencil, Plus, Trash2, UserPlus, UserRound } from "lucide-react";

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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  createTenant,
  deleteTenant,
  fetchTenants,
  updateTenant,
  userFriendlyError,
} from "@/lib/api";
import type { Tenant, TenantBinding, TenantCreateUpdate } from "@/lib/types";

interface TenantsSettingsProps {
  token: string;
  baseUrl: string;
}

interface TenantFormState {
  tenantId: string;
  displayName: string;
  bindings: TenantBinding[];
  bdiEnabled: boolean;
  claimableByPairing: boolean;
  permissions: {
    exec: boolean;
    writeFiles: boolean;
    deviceControl: boolean;
  };
}

const EMPTY_FORM: TenantFormState = {
  tenantId: "",
  displayName: "",
  bindings: [],
  bdiEnabled: false,
  claimableByPairing: false,
  permissions: {
    exec: false,
    writeFiles: false,
    deviceControl: false,
  },
};

// Hardcoded channel options — the WebUI is only ever reached via the
// WebSocket port (browser), but tenant bindings may target other channels
// (e.g. family members on Telegram). Keep this list in sync with the
// channels actually enabled in OriginAgent (telegram, websocket, discord).
const CHANNEL_OPTIONS = [
  { value: "telegram", label: "Telegram" },
  { value: "websocket", label: "WebSocket (WebUI)" },
  { value: "discord", label: "Discord" },
];

function tenantToForm(tenant: Tenant): TenantFormState {
  return {
    tenantId: tenant.tenantId,
    displayName: tenant.displayName,
    bindings: tenant.bindings.map((b) => ({ ...b })),
    bdiEnabled: tenant.bdiEnabled,
    claimableByPairing: tenant.claimableByPairing,
    permissions: { ...tenant.permissions },
  };
}

function formToPayload(form: TenantFormState): TenantCreateUpdate {
  return {
    tenantId: form.tenantId.trim(),
    displayName: form.displayName.trim(),
    bindings: form.bindings
      .map((b) => ({
        channel: b.channel.trim(),
        senderId: b.senderId.trim(),
        label: b.label.trim(),
      }))
      .filter((b) => b.channel !== "" || b.senderId !== "" || b.label !== ""),
    bdiEnabled: form.bdiEnabled,
    claimableByPairing: form.claimableByPairing,
    permissions: { ...form.permissions },
  };
}

/** Template for the "Add Myself" quick button — owner pre-binding. */
const SELF_TEMPLATE: TenantFormState = {
  tenantId: "",
  displayName: "",
  bindings: [{ channel: "telegram", senderId: "", label: "" }],
  bdiEnabled: true,
  claimableByPairing: false,
  permissions: { exec: true, writeFiles: true, deviceControl: true },
};

/** Template for the "Add Family Member" quick button — family claimable. */
const FAMILY_TEMPLATE: TenantFormState = {
  tenantId: "",
  displayName: "",
  bindings: [],
  bdiEnabled: false,
  claimableByPairing: true,
  permissions: { exec: false, writeFiles: false, deviceControl: false },
};

export function TenantsSettings({ token, baseUrl }: TenantsSettingsProps) {
  const { t } = useTranslation();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingTenantId, setEditingTenantId] = useState<string | null>(null);
  const [form, setForm] = useState<TenantFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Tenant | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadTenants = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchTenants(token, baseUrl);
      setTenants(payload.tenants ?? []);
    } catch (err) {
      setError(userFriendlyError(err));
    } finally {
      setLoading(false);
    }
  }, [token, baseUrl]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchTenants(token, baseUrl)
      .then((payload) => {
        if (!cancelled) setTenants(payload.tenants ?? []);
      })
      .catch((err) => {
        if (!cancelled) setError(userFriendlyError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, baseUrl]);

  const openCreate = useCallback((template: TenantFormState = EMPTY_FORM) => {
    setEditingTenantId(null);
    // Clone so editing the form doesn't mutate the template constant.
    setForm({
      ...template,
      permissions: { ...template.permissions },
      bindings: template.bindings.map((b) => ({ ...b })),
    });
    setFormError(null);
    setDialogOpen(true);
  }, []);

  const openEdit = useCallback((tenant: Tenant) => {
    setEditingTenantId(tenant.tenantId);
    setForm(tenantToForm(tenant));
    setFormError(null);
    setDialogOpen(true);
  }, []);

  const closeDialog = useCallback(() => {
    setDialogOpen(false);
    setFormError(null);
  }, []);

  const submitForm = useCallback(async () => {
    const payload = formToPayload(form);
    if (!payload.tenantId) {
      setFormError(t("settings.tenants.errors.tenantIdRequired"));
      return;
    }
    if (!payload.displayName) {
      setFormError(t("settings.tenants.errors.displayNameRequired"));
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      if (editingTenantId) {
        await updateTenant(token, editingTenantId, payload, baseUrl);
      } else {
        await createTenant(token, payload, baseUrl);
      }
      setDialogOpen(false);
      await loadTenants();
    } catch (err) {
      setFormError(
        err instanceof ApiError
          ? err.message
          : editingTenantId
            ? t("settings.tenants.updateFailed")
            : t("settings.tenants.createFailed"),
      );
    } finally {
      setSaving(false);
    }
  }, [editingTenantId, form, loadTenants, baseUrl, t, token]);

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteTenant(token, deleteTarget.tenantId, baseUrl);
      setDeleteTarget(null);
      await loadTenants();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : t("settings.tenants.deleteFailed"),
      );
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, loadTenants, baseUrl, t, token]);

  const isEditing = editingTenantId !== null;

  return (
    <div className="space-y-5">
      {/* Concept intro card */}
      <section className="rounded-[18px] border border-border/45 bg-card/70 px-4 py-4 text-[12px] text-muted-foreground shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]">
        <div className="mb-2 flex items-center gap-2 text-foreground">
          <Info className="h-4 w-4" aria-hidden />
          <h3 className="text-[13px] font-semibold tracking-[-0.01em]">
            {t("settings.tenants.intro.title")}
          </h3>
        </div>
        <p className="mb-3 leading-relaxed">{t("settings.tenants.intro.body")}</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-[12px] border border-border/50 bg-muted/30 px-3 py-2">
            <p className="mb-1 font-medium text-foreground">
              {t("settings.tenants.intro.ownerTitle")}
            </p>
            <p className="leading-relaxed">{t("settings.tenants.intro.ownerBody")}</p>
          </div>
          <div className="rounded-[12px] border border-border/50 bg-muted/30 px-3 py-2">
            <p className="mb-1 font-medium text-foreground">
              {t("settings.tenants.intro.familyTitle")}
            </p>
            <p className="leading-relaxed">{t("settings.tenants.intro.familyBody")}</p>
          </div>
        </div>
      </section>

      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-foreground">
              {t("settings.tenants.title")}
            </h2>
            <p className="mt-1 text-[12px] text-muted-foreground">
              {t("settings.tenants.description")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="default"
              className="rounded-full"
              onClick={() => openCreate(SELF_TEMPLATE)}
            >
              <UserRound className="mr-1 h-3.5 w-3.5" aria-hidden />
              {t("settings.tenants.addSelf")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="rounded-full"
              onClick={() => openCreate(FAMILY_TEMPLATE)}
            >
              <UserPlus className="mr-1 h-3.5 w-3.5" aria-hidden />
              {t("settings.tenants.addFamily")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="rounded-full"
              onClick={() => openCreate(EMPTY_FORM)}
            >
              <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />
              {t("settings.tenants.add")}
            </Button>
          </div>
        </div>

        {loading ? (
          <div className="flex h-32 items-center justify-center rounded-[18px] border border-border/50 bg-card/70 text-[13px] text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
            {t("settings.tenants.loading")}
          </div>
        ) : error ? (
          <div className="rounded-[18px] border border-destructive/20 bg-destructive/5 px-4 py-3 text-[13px] text-destructive">
            {error}
          </div>
        ) : tenants.length === 0 ? (
          <div className="rounded-[18px] border border-dashed border-border/60 bg-card/45 px-4 py-5 text-[13px] text-muted-foreground">
            {t("settings.tenants.empty")}
          </div>
        ) : (
          <div className="overflow-hidden rounded-[18px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px]">
                <thead className="bg-muted/40 text-[11px] uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">{t("settings.tenants.tenantId")}</th>
                    <th className="px-4 py-2.5 font-medium">{t("settings.tenants.displayName")}</th>
                    <th className="px-4 py-2.5 font-medium">{t("settings.tenants.bindings")}</th>
                    <th className="px-4 py-2.5 font-medium">{t("settings.tenants.bdiEnabled")}</th>
                    <th className="px-4 py-2.5 font-medium">{t("settings.tenants.claimableByPairing")}</th>
                    <th className="px-4 py-2.5 text-right font-medium">{t("settings.tenants.actionsLabel")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/45">
                  {tenants.map((tenant) => (
                    <tr key={tenant.tenantId} className="align-top">
                      <td className="px-4 py-3 font-mono text-[12px] text-foreground/85">
                        {tenant.tenantId}
                      </td>
                      <td className="px-4 py-3 text-foreground">{tenant.displayName}</td>
                      <td className="px-4 py-3">
                        {tenant.bindings.length === 0 ? (
                          <span className="text-muted-foreground">—</span>
                        ) : (
                          <div className="flex flex-col gap-1">
                            {tenant.bindings.map((binding, idx) => (
                              <span
                                key={`${binding.channel}-${binding.senderId}-${idx}`}
                                className="inline-flex w-fit items-center gap-1 rounded-full bg-muted/60 px-2 py-0.5 text-[11px] text-foreground/80"
                              >
                                <span className="font-medium">{binding.channel}</span>
                                <span className="text-muted-foreground">:</span>
                                <span className="font-mono">{binding.senderId}</span>
                                {binding.label ? (
                                  <>
                                    <span className="text-muted-foreground">·</span>
                                    <span>{binding.label}</span>
                                  </>
                                ) : null}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <BooleanBadge value={tenant.bdiEnabled} />
                      </td>
                      <td className="px-4 py-3">
                        <BooleanBadge value={tenant.claimableByPairing} />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1.5">
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="h-7 px-2 text-[12px]"
                            onClick={() => openEdit(tenant)}
                          >
                            <Pencil className="mr-1 h-3 w-3" aria-hidden />
                            {t("settings.tenants.edit")}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            className="h-7 px-2 text-[12px] text-destructive hover:text-destructive"
                            onClick={() => setDeleteTarget(tenant)}
                          >
                            <Trash2 className="mr-1 h-3 w-3" aria-hidden />
                            {t("settings.tenants.delete")}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      {/* Create / Edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={(open) => (open ? setDialogOpen(true) : closeDialog())}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {isEditing ? t("settings.tenants.editTitle") : t("settings.tenants.add")}
            </DialogTitle>
            <DialogDescription>{t("settings.tenants.description")}</DialogDescription>
          </DialogHeader>

          <div className="max-h-[60vh] space-y-5 overflow-y-auto pr-1">
            {/* Identity section */}
            <div className="space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {t("settings.tenants.tenantId")}
                  </span>
                  <Input
                    value={form.tenantId}
                    onChange={(e) => setForm((prev) => ({ ...prev, tenantId: e.target.value }))}
                    disabled={isEditing}
                    placeholder={t("settings.tenants.tenantIdPlaceholder")}
                  />
                  <HelpText>{t("settings.tenants.tenantIdHelp")}</HelpText>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    {t("settings.tenants.displayName")}
                  </span>
                  <Input
                    value={form.displayName}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, displayName: e.target.value }))
                    }
                    placeholder={t("settings.tenants.displayNamePlaceholder")}
                  />
                  <HelpText>{t("settings.tenants.displayNameHelp")}</HelpText>
                </label>
              </div>
            </div>

            {/* Bindings section */}
            <div className="space-y-2">
              <FieldLabel
                label={t("settings.tenants.bindings")}
                help={t("settings.tenants.bindingsHelp")}
              />
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-medium text-muted-foreground" />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-7 px-2 text-[12px]"
                  onClick={() =>
                    setForm((prev) => ({
                      ...prev,
                      bindings: [...prev.bindings, { channel: "telegram", senderId: "", label: "" }],
                    }))
                  }
                >
                  <Plus className="mr-1 h-3 w-3" aria-hidden />
                  {t("settings.tenants.addBinding")}
                </Button>
              </div>
              {form.bindings.length === 0 ? (
                <p className="text-[12px] text-muted-foreground">
                  {t("settings.tenants.noBindings")}
                </p>
              ) : (
                <div className="space-y-2">
                  {form.bindings.map((binding, idx) => (
                    <div
                      key={idx}
                      className="grid grid-cols-1 gap-2 rounded-[12px] border border-border/50 bg-muted/30 p-2 sm:grid-cols-[1fr_1fr_1fr_auto]"
                    >
                      <label className="flex flex-col gap-1">
                        <span className="text-[11px] text-muted-foreground">
                          {t("settings.tenants.channel")}
                        </span>
                        <select
                          value={binding.channel}
                          onChange={(e) => {
                            const next = [...form.bindings];
                            next[idx] = { ...next[idx], channel: e.target.value };
                            setForm((prev) => ({ ...prev, bindings: next }));
                          }}
                          className="h-8 rounded-md border border-input bg-background px-2 text-[12px]"
                        >
                          <option value="">{t("settings.tenants.channel")}…</option>
                          {CHANNEL_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="text-[11px] text-muted-foreground">
                          {t("settings.tenants.senderId")}
                        </span>
                        <Input
                          value={binding.senderId}
                          onChange={(e) => {
                            const next = [...form.bindings];
                            next[idx] = { ...next[idx], senderId: e.target.value };
                            setForm((prev) => ({ ...prev, bindings: next }));
                          }}
                          placeholder={t("settings.tenants.senderIdPlaceholder")}
                          className="h-8"
                        />
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="text-[11px] text-muted-foreground">
                          {t("settings.tenants.label")}
                        </span>
                        <Input
                          value={binding.label}
                          onChange={(e) => {
                            const next = [...form.bindings];
                            next[idx] = { ...next[idx], label: e.target.value };
                            setForm((prev) => ({ ...prev, bindings: next }));
                          }}
                          placeholder={t("settings.tenants.labelPlaceholder")}
                          className="h-8"
                        />
                      </label>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="mt-[18px] h-8 px-2 text-destructive hover:text-destructive"
                        onClick={() =>
                          setForm((prev) => ({
                            ...prev,
                            bindings: prev.bindings.filter((_, i) => i !== idx),
                          }))
                        }
                        aria-label={t("settings.tenants.removeBinding")}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
              <HelpText>{t("settings.tenants.senderIdHelp")}</HelpText>
            </div>

            {/* Cognition + pairing toggles */}
            <div className="space-y-2">
              <FieldLabel
                label={t("settings.tenants.bdiEnabled")}
                help={t("settings.tenants.bdiEnabledHelp")}
              />
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <ToggleRow
                  label={t("settings.tenants.bdiEnabled")}
                  checked={form.bdiEnabled}
                  onChange={(checked) => setForm((prev) => ({ ...prev, bdiEnabled: checked }))}
                />
                <ToggleRow
                  label={t("settings.tenants.claimableByPairing")}
                  checked={form.claimableByPairing}
                  onChange={(checked) =>
                    setForm((prev) => ({ ...prev, claimableByPairing: checked }))
                  }
                />
              </div>
              <HelpText>{t("settings.tenants.claimableByPairingHelp")}</HelpText>
            </div>

            {/* Permissions */}
            <div className="space-y-2">
              <FieldLabel
                label={t("settings.tenants.permissions")}
                help={t("settings.tenants.permissionsHelp")}
              />
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <ToggleRow
                  label={t("settings.tenants.exec")}
                  checked={form.permissions.exec}
                  onChange={(checked) =>
                    setForm((prev) => ({
                      ...prev,
                      permissions: { ...prev.permissions, exec: checked },
                    }))
                  }
                />
                <ToggleRow
                  label={t("settings.tenants.writeFiles")}
                  checked={form.permissions.writeFiles}
                  onChange={(checked) =>
                    setForm((prev) => ({
                      ...prev,
                      permissions: { ...prev.permissions, writeFiles: checked },
                    }))
                  }
                />
                <ToggleRow
                  label={t("settings.tenants.deviceControl")}
                  checked={form.permissions.deviceControl}
                  onChange={(checked) =>
                    setForm((prev) => ({
                      ...prev,
                      permissions: { ...prev.permissions, deviceControl: checked },
                    }))
                  }
                />
              </div>
            </div>

            {formError ? (
              <p className="text-[12px] text-destructive">{formError}</p>
            ) : null}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={closeDialog}
              disabled={saving}
            >
              {t("settings.tenants.cancel")}
            </Button>
            <Button
              type="button"
              onClick={submitForm}
              disabled={saving}
            >
              {saving ? t("settings.tenants.saving") : t("settings.tenants.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deleting) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("settings.tenants.confirmDelete")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("settings.tenants.deleteWarning", { id: deleteTarget?.tenantId ?? "" })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>
              {t("settings.tenants.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? t("settings.tenants.deleting") : t("settings.tenants.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function FieldLabel({ label, help }: { label: string; help?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[13px] font-semibold text-foreground">{label}</span>
      {help ? <HelpText>{help}</HelpText> : null}
    </div>
  );
}

function HelpText({ children }: { children: ReactNode }) {
  return (
    <span className="block text-[11px] leading-relaxed text-muted-foreground">
      {children}
    </span>
  );
}

function BooleanBadge({ value }: { value: boolean }) {
  const { t } = useTranslation();
  return (
    <span
      className={
        value
          ? "inline-flex w-fit items-center rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300"
          : "inline-flex w-fit items-center rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
      }
    >
      {value ? t("settings.tenants.values.yes") : t("settings.tenants.values.no")}
    </span>
  );
}

function ToggleRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-3 rounded-[12px] border border-border/50 bg-muted/30 px-3 py-2">
      <span className="text-[12px] font-medium text-foreground/85">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={
          "inline-flex h-6 w-11 items-center rounded-full p-0.5 transition-colors " +
          (checked ? "bg-primary" : "bg-muted")
        }
      >
        <span
          className={
            "h-5 w-5 rounded-full bg-background shadow-sm transition-transform " +
            (checked ? "translate-x-5" : "")
          }
        />
      </button>
    </label>
  );
}

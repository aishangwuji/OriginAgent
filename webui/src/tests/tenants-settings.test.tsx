import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import { TenantsSettings } from "@/components/settings/TenantsSettings";
import * as api from "@/lib/api";
import type { TenantsListResponse, Tenant } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchTenants: vi.fn(),
    createTenant: vi.fn(),
    updateTenant: vi.fn(),
    deleteTenant: vi.fn(),
  };
});

function makeTenant(overrides: Partial<Tenant> = {}): Tenant {
  return {
    tenantId: "dad",
    displayName: "Dad",
    bindings: [
      { channel: "telegram", senderId: "12345", label: "Phone" },
    ],
    bdiEnabled: true,
    claimableByPairing: false,
    permissions: { exec: false, writeFiles: false, deviceControl: true },
    ...overrides,
  };
}

function makeListResponse(
  tenants: Tenant[] = [makeTenant()],
  overrides: Partial<TenantsListResponse> = {},
): TenantsListResponse {
  return {
    tenants,
    guestTenantEnabled: false,
    defaultTenantId: "dad",
    ...overrides,
  };
}

describe("TenantsSettings", () => {
  beforeEach(() => {
    vi.mocked(api.fetchTenants).mockReset();
    vi.mocked(api.createTenant).mockReset();
    vi.mocked(api.updateTenant).mockReset();
    vi.mocked(api.deleteTenant).mockReset();
  });

  it("renders the tenant list after loading", async () => {
    vi.mocked(api.fetchTenants).mockResolvedValue(
      makeListResponse([
        makeTenant(),
        makeTenant({
          tenantId: "mom",
          displayName: "Mom",
          bindings: [],
          bdiEnabled: false,
          claimableByPairing: true,
          permissions: { exec: true, writeFiles: false, deviceControl: false },
        }),
      ]),
    );

    render(<TenantsSettings token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("dad")).toBeInTheDocument();
    });
    expect(screen.getByText("Dad")).toBeInTheDocument();
    expect(screen.getByText("mom")).toBeInTheDocument();
    expect(screen.getByText("Mom")).toBeInTheDocument();
    // binding chip "telegram:12345"
    expect(screen.getByText("12345")).toBeInTheDocument();
    expect(screen.getByText("Phone")).toBeInTheDocument();
  });

  it("opens the create dialog when Add Tenant is clicked", async () => {
    vi.mocked(api.fetchTenants).mockResolvedValue(makeListResponse([]));

    render(<TenantsSettings token="tok" baseUrl="" />);

    // wait for empty state to appear so we know loading finished
    await waitFor(() => {
      expect(
        screen.getByText(/No tenants yet/i),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Add Tenant/i }));

    // Dialog title is the same as the button text — verify the dialog body is visible
    // by checking for the Save button which only appears inside the dialog.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Save$/i })).toBeInTheDocument();
    });
    // The tenantId input is enabled in create mode (not editing).
    // Match the tenantId placeholder specifically — displayName also contains
    // "Dad" (case-insensitive) so we anchor on the leading "e.g. ".
    // Case-sensitive: matches "e.g. dad" (tenantId) but not "e.g. Dad" (displayName).
    const tenantIdInput = screen.getByPlaceholderText(/^e\.g\. dad$/);
    expect(tenantIdInput).not.toBeDisabled();
  });

  it("opens a confirmation dialog when delete is clicked", async () => {
    const tenant = makeTenant();
    vi.mocked(api.fetchTenants).mockResolvedValue(makeListResponse([tenant]));

    render(<TenantsSettings token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("dad")).toBeInTheDocument();
    });

    // Each row has Edit + Delete buttons. Click the delete button for this row.
    const deleteButtons = screen.getAllByRole("button", { name: /Delete/i });
    expect(deleteButtons.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(deleteButtons[0]);

    // AlertDialog should appear with the confirm title.
    await waitFor(() => {
      expect(screen.getByText("Confirm Delete Tenant")).toBeInTheDocument();
    });
    // The tenant id "dad" appears both in the table row (tenantId column) and in
    // the dialog description ("Tenant ID: dad"), so use getAllByText.
    expect(screen.getAllByText(/dad/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: /^Cancel$/i })).toBeInTheDocument();
  });

  it("calls deleteTenant when the confirmation action is clicked", async () => {
    const tenant = makeTenant();
    vi.mocked(api.fetchTenants)
      .mockResolvedValueOnce(makeListResponse([tenant]))
      .mockResolvedValueOnce(makeListResponse([]));
    vi.mocked(api.deleteTenant).mockResolvedValue(undefined);

    render(<TenantsSettings token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("dad")).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByRole("button", { name: /Delete/i })[0]);

    await waitFor(() => {
      expect(screen.getByText("Confirm Delete Tenant")).toBeInTheDocument();
    });

    // AlertDialogAction renders as a button with the "Delete" label.
    // The delete button inside the dialog has the destructive styling.
    const confirmButtons = screen.getAllByRole("button", { name: /^Delete$/i });
    // Click the last matching button (the AlertDialogAction one, not the row button).
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => {
      expect(api.deleteTenant).toHaveBeenCalledWith("tok", "dad", "");
    });
  });

  it("shows an error banner when fetchTenants fails", async () => {
    // Status 599 is outside userFriendlyError's mapped set, so the helper falls
    // back to "Unexpected error (599). Please try again." — exercising the
    // error path without coupling the assertion to a specific mapped message.
    vi.mocked(api.fetchTenants).mockRejectedValue(
      new api.ApiError(599, "boom"),
    );

    render(<TenantsSettings token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText(/Unexpected error/i)).toBeInTheDocument();
    });
  });
});

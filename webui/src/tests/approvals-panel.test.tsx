import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import { ApprovalsPanel } from "@/components/approvals/ApprovalsPanel";
import * as api from "@/lib/api";
import type { Approval } from "@/lib/api";
import type { TenantsListResponse } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchTenants: vi.fn(),
    listApprovals: vi.fn(),
    approveApproval: vi.fn(),
    rejectApproval: vi.fn(),
  };
});

function makeApproval(overrides: Partial<Approval> = {}): Approval {
  return {
    confirmation_id: "confirmation_abc",
    kind: "tool_approval",
    prompt: "Run rm -rf /tmp/test",
    action: "shell.exec",
    risk: "high",
    created_at: "2026-07-19T10:00:00Z",
    expires_at: "2026-07-19T10:02:00Z",
    owner_id: "tenant:owner",
    metadata: { tool_name: "shell" },
    ...overrides,
  };
}

function makeTenantsResponse(defaultTenantId = "tenant:owner"): TenantsListResponse {
  return {
    tenants: [],
    guestTenantEnabled: false,
    defaultTenantId,
  };
}

describe("ApprovalsPanel", () => {
  beforeEach(() => {
    vi.mocked(api.fetchTenants).mockReset();
    vi.mocked(api.listApprovals).mockReset();
    vi.mocked(api.approveApproval).mockReset();
    vi.mocked(api.rejectApproval).mockReset();
    // Default: fetchTenants succeeds with an owner tenant_id so the panel
    // can proceed to call listApprovals.
    vi.mocked(api.fetchTenants).mockResolvedValue(makeTenantsResponse());
  });

  it("renders pending approvals after loading", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue([
      makeApproval({ confirmation_id: "conf_1", prompt: "First approval prompt" }),
      makeApproval({ confirmation_id: "conf_2", prompt: "Second approval prompt" }),
    ]);

    render(<ApprovalsPanel token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("conf_1")).toBeInTheDocument();
    });
    expect(screen.getByText("conf_2")).toBeInTheDocument();
    expect(screen.getByText("First approval prompt")).toBeInTheDocument();
    expect(screen.getByText("Second approval prompt")).toBeInTheDocument();
    // tool_name from metadata renders in both rows — use AllBy variant.
    expect(screen.getAllByText("shell")).toHaveLength(2);
  });

  it("displays empty state when no approvals", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue([]);

    render(<ApprovalsPanel token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("No pending approvals")).toBeInTheDocument();
    });
  });

  it("calls approveApproval and removes the item when Approve is clicked", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue([
      makeApproval({ confirmation_id: "conf_approve" }),
    ]);
    vi.mocked(api.approveApproval).mockResolvedValue({ grantId: "grant_1" });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ApprovalsPanel token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("conf_approve")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Approve/i }));

    await waitFor(() => {
      expect(api.approveApproval).toHaveBeenCalledWith(
        "tok",
        "conf_approve",
        "tenant:owner",
        "",
      );
    });
    // Item removed from list after successful approve.
    await waitFor(() => {
      expect(screen.queryByText("conf_approve")).not.toBeInTheDocument();
    });

    confirmSpy.mockRestore();
  });

  it("calls rejectApproval and removes the item when Reject is clicked", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue([
      makeApproval({ confirmation_id: "conf_reject" }),
    ]);
    vi.mocked(api.rejectApproval).mockResolvedValue(undefined);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ApprovalsPanel token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("conf_reject")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Reject/i }));

    await waitFor(() => {
      expect(api.rejectApproval).toHaveBeenCalledWith(
        "tok",
        "conf_reject",
        "tenant:owner",
        "",
      );
    });
    await waitFor(() => {
      expect(screen.queryByText("conf_reject")).not.toBeInTheDocument();
    });

    confirmSpy.mockRestore();
  });

  it("shows permission-denied error when list returns 403", async () => {
    // 规则 26 偏离声明: spec 原本要求 test_guest_role_hides_panel，但 WebUI
    // 没有 useAuth/useUser role context（auth 是单 token 模型）。改测 403 错误
    // 路径，覆盖跨 owner 审批被后端拒绝时的 UI 表现。
    vi.mocked(api.listApprovals).mockRejectedValue(
      new api.ApiError(403, "forbidden"),
    );

    render(<ApprovalsPanel token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("Permission denied")).toBeInTheDocument();
    });
  });

  it("does not call approveApproval when user cancels confirm dialog", async () => {
    vi.mocked(api.listApprovals).mockResolvedValue([
      makeApproval({ confirmation_id: "conf_cancel" }),
    ]);
    vi.mocked(api.approveApproval).mockResolvedValue({ grantId: "g" });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<ApprovalsPanel token="tok" baseUrl="" />);

    await waitFor(() => {
      expect(screen.getByText("conf_cancel")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Approve/i }));

    // Give the async handler a tick to potentially fire.
    await new Promise((r) => setTimeout(r, 0));
    expect(api.approveApproval).not.toHaveBeenCalled();
    expect(screen.getByText("conf_cancel")).toBeInTheDocument();

    confirmSpy.mockRestore();
  });
});

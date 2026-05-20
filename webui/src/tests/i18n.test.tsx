import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import { resources } from "@/i18n";

const QUICK_ACTION_KEYS = ["plan", "analyze", "brainstorm", "code", "summarize", "more"];
const IMAGE_QUICK_ACTION_KEYS = ["icon", "sticker", "poster", "product", "portrait", "edit"];
const SETTINGS_NAV_KEYS = ["general", "byok", "skills", "mcp"];
const REVIEW_STATUS_KEYS = ["pending", "applied", "rejected", "deferred", "failed"];
const REVIEW_TYPE_KEYS = [
  "memory",
  "fact",
  "skill",
  "workflow",
  "promote_skill",
  "deprecate_skill",
  "merge_skill",
  "archive_workflow",
  "move_to_domain",
  "fact_conflict",
];
const REVIEW_ORIGIN_KEYS = ["background_review", "curator"];

describe("webui i18n", () => {
  it("switches UI copy and document locale through the language switcher", async () => {
    const user = userEvent.setup();

    render(
      <>
        <LanguageSwitcher />
        <ThreadComposer onSend={vi.fn()} />
      </>,
    );

    expect(
      screen.getByPlaceholderText("Type your message…"),
    ).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");

    await user.click(screen.getByRole("button", { name: "Change language" }));
    await user.click(screen.getByRole("menuitemradio", { name: /简体中文/i }));

    await waitFor(() => {
      expect(document.documentElement.lang).toBe("zh-CN");
    });
    expect(localStorage.getItem("OpenHome.locale")).toBe("zh-CN");
    expect(screen.getByPlaceholderText("输入消息…")).toBeInTheDocument();
  });

  it("updates the composer aria label when the language changes", async () => {
    render(<ThreadComposer onSend={vi.fn()} />);

    await act(async () => {
      const { setAppLanguage } = await import("@/i18n");
      await setAppLanguage("ja");
    });

    expect(screen.getByLabelText("メッセージ入力欄")).toBeInTheDocument();
  });

  it("keeps welcome quick actions localized for every registered locale", () => {
    for (const resource of Object.values(resources)) {
      const empty = resource.common.thread.empty;
      expect(empty.greeting).toBeTruthy();
      for (const key of QUICK_ACTION_KEYS) {
        const action = empty.quickActions[key as keyof typeof empty.quickActions];
        expect(action.title).toBeTruthy();
        expect(action.prompt).toBeTruthy();
      }
      for (const key of IMAGE_QUICK_ACTION_KEYS) {
        const action = empty.imageQuickActions[key as keyof typeof empty.imageQuickActions];
        expect(action.title).toBeTruthy();
        expect(action.prompt).toBeTruthy();
      }
    }
  });

  it("keeps settings navigation localized for every registered locale", () => {
    for (const resource of Object.values(resources)) {
      const common = resource.common;
      expect(common.app.system.restarting).toBeTruthy();
      expect(common.sidebar.settings).toBeTruthy();
      expect(common.settings.sidebar.title).toBeTruthy();
      expect(common.settings.backToChat).toBeTruthy();
      for (const key of SETTINGS_NAV_KEYS) {
        expect(common.settings.nav[key as keyof typeof common.settings.nav]).toBeTruthy();
      }
      expect(common.settings.rows.theme).toBeTruthy();
      expect(common.settings.status.loading).toBeTruthy();
      expect(common.settings.actions.save).toBeTruthy();
      expect(common.settings.actions.edit).toBeTruthy();
      expect(common.settings.byok.configured).toBeTruthy();
      expect(common.settings.byok.configuredSection).toBeTruthy();
      expect(common.settings.byok.showMore).toBeTruthy();
      expect(common.settings.byok.apiKeyRequired).toBeTruthy();
      expect(common.settings.byok.showApiKey).toBeTruthy();
      expect(common.settings.byok.hideApiKey).toBeTruthy();
      expect(common.settings.byok.configuredKeyHint).toBeTruthy();
      expect(common.settings.skills.description).toBeTruthy();
      expect(common.settings.skills.actions.verify).toBeTruthy();
      expect(common.settings.skills.actions.activate).toBeTruthy();
      expect(common.settings.skills.status.proposed).toBeTruthy();
      expect(common.settings.skills.verification.verified).toBeTruthy();
      expect(common.settings.mcp.add).toBeTruthy();
      expect(common.settings.mcp.quickConfig).toBeTruthy();
      expect(common.settings.mcp.homeAssistant.title).toBeTruthy();
      expect(common.settings.mcp.homeAssistant.validation.tokenRequired).toBeTruthy();
      expect(common.settings.mcp.validation.nameRequired).toBeTruthy();
    }
  });

  it("ships domains settings copy in English and Simplified Chinese", () => {
    for (const locale of ["en", "zh-CN"] as const) {
      const common = resources[locale].common;
      expect(common.settings.nav.domains).toBeTruthy();
      expect(common.settings.domains.description).toBeTruthy();
      expect(common.settings.domains.install.title).toBeTruthy();
      expect(common.settings.domains.install.placeholder).toBeTruthy();
      expect(common.settings.domains.actions.install).toBeTruthy();
      expect(common.settings.domains.actions.eval).toBeTruthy();
      expect(common.settings.domains.status.available).toBeTruthy();
      expect(common.settings.domains.status.invalid).toBeTruthy();
      expect(common.settings.domains.source.workspace).toBeTruthy();
      expect(common.settings.domains.fields.version).toBeTruthy();
      expect(common.settings.domains.validation.upgradePathRequired).toBeTruthy();
      expect(common.settings.domains.confirm.activate).toBeTruthy();
      expect(common.settings.domains.confirm.uninstall).toBeTruthy();
    }
  });

  it("keeps review navigation and actions localized for every registered locale", () => {
    for (const resource of Object.values(resources)) {
      const common = resource.common;
      expect(common.sidebar.reviews).toBeTruthy();
      expect(common.reviews.title).toBeTruthy();
      expect(common.reviews.backToChat).toBeTruthy();
      expect(common.reviews.loading).toBeTruthy();
      expect(common.reviews.empty).toBeTruthy();
      expect(common.reviews.unsupportedApply).toBeTruthy();
      expect(common.reviews.appliedSkill).toBeTruthy();
      expect(common.reviews.appliedWorkflow).toBeTruthy();
      expect(common.reviews.actions.apply).toBeTruthy();
      expect(common.reviews.actions.reject).toBeTruthy();
      expect(common.reviews.actions.defer).toBeTruthy();
      expect(common.reviews.filters.origin).toBeTruthy();
      expect(common.reviews.filters.allOrigins).toBeTruthy();
      expect(common.reviews.confirmApply.title).toBeTruthy();
      expect(common.reviews.confirmApply.skillDescription).toBeTruthy();
      expect(common.reviews.confirmApply.promoteSkillDescription).toBeTruthy();
      expect(common.reviews.confirmApply.deprecateSkillDescription).toBeTruthy();
      expect(common.reviews.confirmApply.skillPathPending).toBeTruthy();
      expect(common.reviews.confirmApply.workflowDescription).toBeTruthy();
      expect(common.reviews.confirmApply.workflowPathPending).toBeTruthy();
      expect(common.reviews.confirmApply.confirm).toBeTruthy();
      expect(common.reviews.fields.origin).toBeTruthy();
      expect(common.reviews.fields.subject).toBeTruthy();
      expect(common.reviews.fields.suggestedAction).toBeTruthy();
      for (const key of REVIEW_STATUS_KEYS) {
        expect(common.reviews.status[key as keyof typeof common.reviews.status]).toBeTruthy();
      }
      for (const key of REVIEW_TYPE_KEYS) {
        expect(common.reviews.types[key as keyof typeof common.reviews.types]).toBeTruthy();
      }
      for (const key of REVIEW_ORIGIN_KEYS) {
        expect(common.reviews.origins[key as keyof typeof common.reviews.origins]).toBeTruthy();
      }
    }
  });
});

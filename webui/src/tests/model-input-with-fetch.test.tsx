import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ModelInputWithFetch } from "@/components/settings/ModelInputWithFetch";

describe("ModelInputWithFetch", () => {
  it("allows manual input and fetch button click", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onFetch = vi.fn();

    render(
      <ModelInputWithFetch
        value="gpt-4o"
        onChange={onChange}
        onFetch={onFetch}
        fetchedModels={[]}
        providerCatalogKind="official"
        hasFetched={false}
        isLoading={false}
      />,
    );

    await user.type(screen.getByDisplayValue("gpt-4o"), "-mini");
    await user.click(screen.getByRole("button", { name: "Fetch models" }));

    expect(onChange).toHaveBeenCalled();
    expect(onFetch).toHaveBeenCalledTimes(1);
  });

  it("shows dropdown entries when fetched models exist", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    render(
      <ModelInputWithFetch
        value=""
        onChange={onChange}
        onFetch={vi.fn()}
        fetchedModels={[
          { id: "gpt-4o-mini", owned_by: "openai" },
          { id: "gpt-4.1", owned_by: "openai" },
        ]}
        providerCatalogKind="official"
        hasFetched
        isLoading={false}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Open fetched models" }));
    await user.click(screen.getByText("gpt-4o-mini"));

    expect(onChange).toHaveBeenCalledWith("gpt-4o-mini");
  });

  it("disables fetch button while loading", () => {
    render(
      <ModelInputWithFetch
        value=""
        onChange={vi.fn()}
        onFetch={vi.fn()}
        fetchedModels={[]}
        providerCatalogKind="official"
        hasFetched={false}
        isLoading
      />,
    );

    expect(screen.getByRole("button", { name: "Fetching models..." })).toBeDisabled();
  });

  it("shows an empty-state hint after a fetch returns no models", () => {
    render(
      <ModelInputWithFetch
        value=""
        onChange={vi.fn()}
        onFetch={vi.fn()}
        fetchedModels={[]}
        providerCatalogKind="official"
        hasFetched
        isLoading={false}
      />,
    );

    expect(
      screen.getByText("No models were returned. You can still enter one manually."),
    ).toBeInTheDocument();
  });

  it("shows cached state and refresh action when payload metadata exists", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn();

    render(
      <ModelInputWithFetch
        value="gpt-4o-mini"
        onChange={vi.fn()}
        onFetch={vi.fn()}
        onRefresh={onRefresh}
        fetchedModels={[{ id: "gpt-4o-mini", owned_by: "openai" }]}
        fetchPayload={{
          provider: "openai",
          status: "available",
          catalog_kind: "official",
          models: [{ id: "gpt-4o-mini", owned_by: "openai" }],
          model_count: 1,
          fetched_at: 1717171717,
          cached: true,
        }}
        providerCatalogKind="official"
        hasFetched
        isLoading={false}
      />,
    );

    expect(screen.getByText("Using cached model list")).toBeInTheDocument();
    expect(screen.getByText(/Last fetched:/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Refresh models" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("gates catalog fetch until at least two characters are entered", () => {
    render(
      <ModelInputWithFetch
        value="o"
        onChange={vi.fn()}
        onFetch={vi.fn()}
        fetchedModels={[]}
        providerLabel="OpenRouter"
        providerCatalogKind="catalog"
        hasFetched={false}
        isLoading={false}
      />,
    );

    expect(
      screen.getByText("Type at least 2 characters to narrow down OpenRouter models before fetching."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fetch models" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Open fetched models" })).toBeDisabled();
  });

  it("keeps manual entry available for unsupported providers while leaving fetch actions visible", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onFetch = vi.fn();

    render(
      <ModelInputWithFetch
        value="claude-sonnet"
        onChange={onChange}
        onFetch={onFetch}
        fetchedModels={[]}
        providerCatalogKind="unsupported"
        hasFetched={false}
        isLoading={false}
      />,
    );

    expect(
      screen.getByText("Automatic model fetching is not supported for this provider yet."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fetch models" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open fetched models" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Fetch models" }));
    await user.type(screen.getByDisplayValue("claude-sonnet"), "-4");
    expect(onFetch).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalled();
  });
});

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
        hasFetched
        isLoading={false}
      />,
    );

    expect(
      screen.getByText("No models were returned. You can still enter one manually."),
    ).toBeInTheDocument();
  });
});

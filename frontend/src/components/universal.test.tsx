import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppHeader } from "./AppHeader";
import { AskPage } from "./AskPage";
import { PreparePage } from "./PreparePage";

describe("PAW Offline shell", () => {
  it("has exactly Ask and Prepare as primary navigation", () => {
    const change = vi.fn();
    render(
      <AppHeader
        page="ask"
        onPageChange={change}
        onOpenHistory={vi.fn()}
      />,
    );
    const navigation = screen.getByRole("navigation", { name: "Primary" });
    expect(
      within(navigation)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["Ask", "Prepare"]);
    expect(screen.getByLabelText("Prepare for Offline")).toBeInTheDocument();
  });
});

describe("Ask", () => {
  it("asks immediately with no source or preparation controls", () => {
    const submit = vi.fn();
    render(
      <AskPage
        starters={[{ id: "one", text: "What does simida mean?" }]}
        turns={[]}
        value="How do tides work?"
        asking={false}
        nextStartsNewTopic={false}
        onValueChange={vi.fn()}
        onSubmit={submit}
        onNewTopic={vi.fn()}
      />,
    );
    expect(screen.getByText("Ask anything, anywhere.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send question" }));
    expect(submit).toHaveBeenCalledWith("How do tides work?");
    expect(screen.queryByText(/source/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/citation/i)).not.toBeInTheDocument();
  });

  it("shows one answer card while neural programs refine it", () => {
    render(
      <AskPage
        starters={[]}
        turns={[
          {
            id: "turn",
            question: "What is the capital of Georgia?",
            answer: "Tbilisi for the country; Atlanta for the U.S. state.",
            state: "working",
            status: "Thinking…",
            refined: false,
          },
        ]}
        value=""
        asking
        nextStartsNewTopic={false}
        onValueChange={vi.fn()}
        onSubmit={vi.fn()}
        onNewTopic={vi.fn()}
      />,
    );
    expect(screen.getAllByText("Thinking…")).not.toHaveLength(0);
    expect(
      screen.getByText("Tbilisi for the country; Atlanta for the U.S. state."),
    ).toBeInTheDocument();
  });
});

describe("Prepare", () => {
  it("contains one topic prompt, one primary action, and no attachments", () => {
    const prepare = vi.fn().mockResolvedValue(undefined);
    render(
      <PreparePage
        programs={[]}
        job={null}
        error={null}
        onPrepare={prepare}
        onCancel={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    const prompt = screen.getByPlaceholderText("Korean language for travel");
    fireEvent.change(prompt, { target: { value: "Ottoman history" } });
    fireEvent.click(screen.getByRole("button", { name: "Prepare" }));
    expect(prepare).toHaveBeenCalledWith("Ottoman history");
    expect(screen.queryByText(/attach/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/source/i)).not.toBeInTheDocument();
  });
});

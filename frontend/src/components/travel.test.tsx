import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AskPage } from "./AskPage";
import { PreparePage } from "./PreparePage";
import { TravelHeader } from "./TravelHeader";

describe("two-page travel shell", () => {
  it("exposes only Ask and Prepare as primary destinations", () => {
    const change = vi.fn();
    render(
      <TravelHeader
        page="ask"
        onPageChange={change}
        onOpenHistory={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );
    const primary = screen.getByRole("navigation", { name: "Primary" });
    expect(primary.querySelectorAll("button")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Prepare" }));
    expect(change).toHaveBeenCalledWith("prepare");
    expect(screen.queryByRole("button", { name: "Packs" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Verify" })).not.toBeInTheDocument();
  });
});

describe("Ask", () => {
  it("keeps preparation as the only empty-trip action", () => {
    const prepare = vi.fn();
    render(
      <AskPage
        trips={[]}
        activeTrip={null}
        starters={[]}
        turns={[]}
        value=""
        asking={false}
        nextStartsNewTopic={true}
        nextFollowUp={0}
        maxFollowUps={3}
        onTripChange={vi.fn()}
        onValueChange={vi.fn()}
        onSubmit={vi.fn()}
        onNewTopic={vi.fn()}
        onPrepare={prepare}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /prepare a trip/i }));
    expect(prepare).toHaveBeenCalledOnce();
  });
});

describe("Prepare", () => {
  it("starts from one natural-language trip field and one primary action", () => {
    const submit = vi.fn();
    render(
      <PreparePage
        trip={null}
        coverage={null}
        phase="idle"
        statusText="Ready to prepare"
        progress={0}
        error={null}
        blockingQuestion={null}
        onSubmit={submit}
        onClarify={vi.fn()}
        onSaveTrip={vi.fn()}
        onCancel={vi.fn()}
        onNewTrip={vi.fn()}
        onReprepare={vi.fn()}
      />,
    );
    const input = screen.getByPlaceholderText("I’m going to ICML 2026 in Seoul");
    fireEvent.change(input, { target: { value: "I'm going to ICML 2026 in Seoul" } });
    fireEvent.click(screen.getByRole("button", { name: /prepare for offline/i }));
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "I'm going to ICML 2026 in Seoul",
        files: [],
      }),
    );
  });
});

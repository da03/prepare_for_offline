import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppHeader } from "./AppHeader";
import { AskPage } from "./AskPage";
import { HistoryDrawer } from "./HistoryDrawer";
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
        turns={[]}
        value="How do tides work?"
        asking={false}
        followUpTarget={null}
        onValueChange={vi.fn()}
        onSubmit={submit}
        onFollowUp={vi.fn()}
        onCancelFollowUp={vi.fn()}
      />,
    );
    expect(screen.getByText("Ask anything, anywhere.")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Ask anything…")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "What does simida mean?" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send question" }));
    expect(submit).toHaveBeenCalledWith("How do tides work?");
    expect(
      screen.queryByRole("button", { name: /new topic/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/source/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/citation/i)).not.toBeInTheDocument();
  });

  it("shows one answer card while neural programs refine it", () => {
    render(
      <AskPage
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
        followUpTarget={null}
        onValueChange={vi.fn()}
        onSubmit={vi.fn()}
        onFollowUp={vi.fn()}
        onCancelFollowUp={vi.fn()}
      />,
    );
    expect(screen.getAllByText("Thinking…")).not.toHaveLength(0);
    expect(
      screen.getByText("Tbilisi for the country; Atlanta for the U.S. state."),
    ).toBeInTheDocument();
  });

  it("labels curated factual answers as verified", () => {
    render(
      <AskPage
        turns={[
          {
            id: "turn",
            question: "What are the major cities of South Korea?",
            answer: "Seoul, Busan, Incheon, Daegu, Daejeon, Gwangju, and Ulsan.",
            state: "complete",
            status: "",
            refined: true,
            support: "prepared_facts",
            sourceLabel: "South Korea",
            answerMessageId: "msg-answer",
          },
        ]}
        value=""
        asking={false}
        followUpTarget={null}
        onValueChange={vi.fn()}
        onSubmit={vi.fn()}
        onFollowUp={vi.fn()}
        onCancelFollowUp={vi.fn()}
      />,
    );
    expect(screen.getByText("Verified facts · South Korea")).toBeInTheDocument();
  });

  it("anchors follow-ups to a chosen answer while new questions stay independent", () => {
    const followUp = vi.fn();
    const cancel = vi.fn();
    render(
      <AskPage
        turns={[
          {
            id: "turn",
            question: "How should I get around Singapore?",
            answer: "Use the MRT for most longer trips.",
            state: "complete",
            status: "",
            refined: false,
            answerMessageId: "msg-answer",
          },
        ]}
        value=""
        asking={false}
        followUpTarget={{
          messageId: "msg-answer",
          question: "How should I get around Singapore?",
        }}
        onValueChange={vi.fn()}
        onSubmit={vi.fn()}
        onFollowUp={followUp}
        onCancelFollowUp={cancel}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", {
        name: "Follow up on: How should I get around Singapore?",
      }),
    );
    expect(followUp).toHaveBeenCalledWith(
      expect.objectContaining({ answerMessageId: "msg-answer" }),
    );
    expect(screen.getByText("Following up on")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Ask a follow-up…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel follow-up" }));
    expect(cancel).toHaveBeenCalledOnce();
  });
});

describe("History", () => {
  it("lists saved root questions without conversation controls", () => {
    render(
      <HistoryDrawer
        conversations={[
          {
            conversation_id: "conv-one",
            title: "How should I get around Singapore?",
            created_at: "2026-07-18T00:00:00Z",
            updated_at: "2026-07-18T00:00:00Z",
            question_count: 2,
          },
        ]}
        activeConversationId={null}
        loading={false}
        onClose={vi.fn()}
        onSearch={vi.fn()}
        onSelect={vi.fn()}
        onDelete={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.getByText(/^2 questions ·/)).toBeInTheDocument();
    expect(screen.queryByText(/new topic/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/messages/i)).not.toBeInTheDocument();
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

  it("shows live compiler progress and elapsed time during finetuning", () => {
    render(
      <PreparePage
        programs={[]}
        job={{
          job_id: "job-one",
          program_key: "topic:singapore",
          topic_prompt: "Singapore",
          state: "compiling_finetuned",
          progress_percent: 50,
          message: "Improving final program",
          created_at: new Date(Date.now() - 65_000).toISOString(),
          updated_at: new Date().toISOString(),
        }}
        error={null}
        onPrepare={vi.fn()}
        onCancel={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByText("Step 2 of 2 · 50%")).toBeInTheDocument();
    expect(screen.getByText("Usually 2–5 minutes")).toBeInTheDocument();
    expect(screen.getByText(/elapsed$/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "50");
  });
});

"use client";

import { CaretDown } from "@phosphor-icons/react";
import clsx from "clsx";
import { useReducedMotion } from "motion/react";
import { useState } from "react";
import { ThinkingOrb } from "@/components/ui/thinking-orbs";

export type TextSegment = { kind: "text" | "thought"; text: string; open: boolean };

const THINK = /<think>([\s\S]*?)(<\/think>|$)/g;

/** Splits agent text into prose and <think> sections. A section with no closing tag is still streaming. */
export function splitThoughts(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  const push = (kind: TextSegment["kind"], body: string, open = false) => {
    const trimmed = body.trim();
    if (trimmed) segments.push({ kind, text: trimmed, open });
  };

  // Some models drop the opening tag and only close the thought.
  let rest = text;
  const close = rest.indexOf("</think>");
  const start = rest.indexOf("<think>");
  if (close !== -1 && (start === -1 || close < start)) {
    push("thought", rest.slice(0, close));
    rest = rest.slice(close + "</think>".length);
  }

  let last = 0;
  for (const match of rest.matchAll(THINK)) {
    push("text", rest.slice(last, match.index));
    push("thought", match[1], match[2] !== "</think>");
    last = match.index + match[0].length;
  }
  push("text", rest.slice(last));
  return segments;
}

/** The agent is thinking right now: the animated orb in a quiet pill. */
export function ThinkingPill({ label = "Thinking…" }: { label?: string }) {
  const reduce = useReducedMotion();
  return (
    <div
      role="status"
      className="inline-flex h-12 items-center gap-2.5 self-start rounded-full pl-1.5 pr-5"
      style={{
        background: "rgba(29,29,29,0.42)",
        boxShadow: "inset 0 0 0 1px rgba(44,47,54,0.31), inset 0 0 50px 0 rgba(255,255,255,0.012)",
      }}
    >
      <span className="grid size-9 place-items-center [&_canvas]:!size-9" aria-hidden>
        <ThinkingOrb state="solving" size={64} theme="dark" paused={reduce ?? false} />
      </span>
      <span className="whitespace-nowrap text-[0.92rem] leading-6 text-fg-muted">{label}</span>
    </div>
  );
}

/** One <think> section. Live while it streams; afterwards it folds into a quiet, expandable row. */
export function ThoughtBlock({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);

  if (live) {
    return (
      <div className="flex flex-col gap-2.5">
        <ThinkingPill />
        <p className="whitespace-pre-wrap border-l border-line pl-4 text-[0.88rem] leading-relaxed text-fg-subtle">{text}</p>
      </div>
    );
  }

  const preview = text.split("\n")[0];
  return (
    <div className="flex flex-col">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-2 rounded-full py-1 text-left text-[0.82rem] text-fg-subtle transition-colors duration-150 hover:text-fg-muted"
      >
        <CaretDown weight="bold" className={clsx("size-3.5 shrink-0 transition-transform duration-200 ease-[var(--ease-out)]", !open && "-rotate-90")} aria-hidden />
        <span className="shrink-0 font-medium">Thought</span>
        {!open && <span className="min-w-0 truncate">{preview}</span>}
      </button>
      <div className={clsx("grid transition-[grid-template-rows] duration-[240ms] ease-[var(--ease-out)]", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
        <div className="min-h-0 overflow-hidden">
          <p className="mt-1.5 whitespace-pre-wrap border-l border-line pl-4 text-[0.88rem] leading-relaxed text-fg-subtle">{text}</p>
        </div>
      </div>
    </div>
  );
}

"use client";

import { ArrowSquareOut, CaretDown } from "@phosphor-icons/react";
import clsx from "clsx";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";
import { AgentText, withoutThoughts } from "@/components/chat/AgentText";
import { helperPhase } from "@/components/chat/Handoff";
import type { ChildRun } from "@/lib/state";

export type RunReceipt = {
  advisories_fixed: number;
  model_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  cost_inr: number | null;
  duration_seconds: number | null;
  trace_url: string | null;
  source: "gateway" | "events";
};

type Phase = { kind: "tallying" } | { kind: "ready"; receipt: RunReceipt } | { kind: "unavailable" };

const RETRY_MS = 3000;
const GIVE_UP_MS = 45000;
// Not finished yet (409) or waiting on lagging gateway logs (202); anything else is final.
const KEEP_WAITING = new Set([202, 409]);

const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${rest}s`;
  return `${rest}s`;
}

export function receiptParts(receipt: RunReceipt): string[] {
  // A cost is shown only when the gateway reported one; missing data never renders as ₹0.00.
  const cost = receipt.source === "gateway" && receipt.cost_inr !== null ? INR.format(receipt.cost_inr) : "cost unavailable";
  const parts = [
    `Fixed ${plural(receipt.advisories_fixed, "advisory", "advisories")}`,
    plural(receipt.model_calls, "model call", "model calls"),
    cost,
  ];
  if (receipt.duration_seconds !== null) parts.push(formatDuration(receipt.duration_seconds));
  return parts;
}

function isReceipt(value: unknown): value is RunReceipt {
  const v = value as Partial<RunReceipt> | null;
  return typeof v?.advisories_fixed === "number" && typeof v.model_calls === "number" && (v.source === "gateway" || v.source === "events");
}

/** The receipt agent's words under the receipt line, folded by default. */
function Breakdown({ helper }: { helper: ChildRun }) {
  const [open, setOpen] = useState(false);
  const phase = helperPhase(helper.status);
  const raw = typeof helper.result?.raw === "string" ? helper.result.raw : null;
  const text = raw !== null ? withoutThoughts(raw).trim() : helper.result ? JSON.stringify(helper.result, null, 2) : "";
  const ready = phase === "done" && text !== "";

  if (!ready) {
    return (
      <p className="flex items-center gap-2 text-[0.85rem] text-fg-subtle">
        {phase === "running" && <span aria-hidden className="dot-pulse size-2 rounded-full bg-progress shadow-[0_0_10px_rgb(255_176_32/0.6)]" />}
        {phase === "failed" ? "The receipt agent stopped before it wrote a breakdown" : phase === "done" ? "The receipt agent wrote no breakdown" : "Writing the breakdown"}
      </p>
    );
  }
  return (
    <div className="flex flex-col">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-fit items-center gap-2 rounded-full py-1 text-[0.88rem] font-medium text-fg-muted transition-colors duration-150 hover:text-fg"
      >
        <CaretDown weight="bold" className={clsx("size-3.5 transition-transform duration-200 ease-[var(--ease-out)]", !open && "-rotate-90")} aria-hidden />
        Breakdown
      </button>
      {/* Accordion: height is the one tolerated layout animation. Grid rows keep it off JS; 240ms ease out. */}
      <div className={clsx("grid transition-[grid-template-rows] duration-[240ms] ease-[var(--ease-out)] motion-reduce:transition-none", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
        <div className="min-h-0 overflow-hidden">
          <div className="mt-2 border-l border-line pl-4">
            <AgentText text={text} />
          </div>
        </div>
      </div>
    </div>
  );
}

export function Receipt({ runId, breakdown = null }: { runId: string; breakdown?: ChildRun | null }) {
  const reduce = useReducedMotion();
  const [phase, setPhase] = useState<Phase>({ kind: "tallying" });

  useEffect(() => {
    const controller = new AbortController();
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const attempt = async () => {
      try {
        const response = await fetch(`/api/receipt?runId=${encodeURIComponent(runId)}`, { cache: "no-store", signal: controller.signal });
        // 202 is ok() too, so match the status exactly: only 200 carries a receipt.
        if (!KEEP_WAITING.has(response.status)) {
          const body: unknown = response.status === 200 ? await response.json() : null;
          if (controller.signal.aborted) return;
          setPhase(isReceipt(body) ? { kind: "ready", receipt: body } : { kind: "unavailable" });
          return;
        }
      } catch {
        // A dropped request is retried like a pending one, within the same 45 second budget.
      }
      if (controller.signal.aborted) return;
      if (Date.now() - started + RETRY_MS > GIVE_UP_MS) {
        setPhase({ kind: "unavailable" });
        return;
      }
      timer = setTimeout(attempt, RETRY_MS);
    };

    attempt();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  if (phase.kind === "tallying") {
    return (
      <p role="status" className="px-5 text-[0.95rem] text-fg-subtle">
        Tallying the run
      </p>
    );
  }

  if (phase.kind === "unavailable") {
    return (
      <p role="status" className="px-5 text-[0.95rem] text-fg-subtle">
        Receipt unavailable
      </p>
    );
  }

  const { receipt } = phase;
  return (
    // Entrance: occasional, state indication. Strong ease out, 220ms, a 4px lift; opacity only under reduced motion.
    <motion.div
      role="status"
      className="flex flex-col gap-1.5 px-5"
      initial={reduce ? { opacity: 0 } : { opacity: 0, transform: "translateY(4px)" }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, transform: "translateY(0px)" }}
      transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
    >
      <p className="font-mono text-[1.05rem] leading-relaxed text-fg">
        {receiptParts(receipt).map((part, i) => (
          <span key={i}>
            {i > 0 && (
              <span aria-hidden className="text-fg-subtle">
                {" · "}
              </span>
            )}
            <span className={part === "cost unavailable" ? "text-fg-muted" : undefined}>{part}</span>
          </span>
        ))}
      </p>
      {receipt.trace_url && (
        <a
          href={receipt.trace_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex w-fit items-center gap-1.5 text-[0.9rem] text-fg-muted underline decoration-line-strong underline-offset-4 transition-colors duration-150 hover:text-fg"
        >
          View trace
          <ArrowSquareOut weight="bold" className="size-4" aria-hidden />
        </a>
      )}
      {breakdown && <Breakdown helper={breakdown} />}
    </motion.div>
  );
}

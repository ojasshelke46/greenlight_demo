"use client";

import { Check, Hand, X } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "motion/react";
import type { Signal, Step } from "@/lib/state";

const SIGNAL_TEXT: Record<Signal, string> = {
  red: "text-signal-red",
  amber: "text-signal-amber",
  green: "text-signal-green",
};

export function Pipeline({ steps }: { steps: Step[] }) {
  return (
    <ol
      aria-label="Pipeline"
      className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-4 lg:[grid-template-columns:repeat(var(--steps),minmax(0,1fr))] lg:gap-0"
      style={{ "--steps": steps.length } as React.CSSProperties}
    >
      {steps.map((step, index) => (
        <PipelineStep key={step.id} step={step} last={index === steps.length - 1} next={steps[index + 1]} />
      ))}
    </ol>
  );
}

function PipelineStep({ step, last, next }: { step: Step; last: boolean; next?: Step }) {
  const reduce = useReducedMotion();
  const reached = step.status === "done" && next !== undefined && next.status !== "waiting";

  return (
    <li className="relative flex flex-col gap-3 lg:pr-3" aria-current={step.status === "active" ? "step" : undefined}>
      <div className="flex items-center">
        <Node step={step} />
        {!last && (
          <div
            aria-hidden
            className={`ml-3 hidden h-0.5 flex-1 lg:block ${reached ? "bg-fg-subtle" : "bg-ink-700"}`}
          />
        )}
      </div>
      <div>
        <p className={`text-lg font-semibold leading-tight ${step.status === "waiting" ? "text-fg-subtle" : "text-fg"}`}>
          {step.label}
        </p>
        {step.detail && (
          <motion.p
            key={step.detail}
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            className={`mt-1 text-base leading-snug ${step.detailSignal ? SIGNAL_TEXT[step.detailSignal] : "text-fg-muted"}`}
          >
            {step.detail}
          </motion.p>
        )}
        {step.status === "needs_you" && !step.detail && (
          <p className="mt-1 text-base text-fg">Waiting for you</p>
        )}
      </div>
    </li>
  );
}

function Node({ step }: { step: Step }) {
  const base = "grid size-9 shrink-0 place-items-center rounded-full";
  switch (step.status) {
    case "done":
      return (
        <span className={`${base} bg-fg text-ink-950`}>
          <Check weight="bold" className="size-5" aria-hidden />
          <span className="sr-only">Done</span>
        </span>
      );
    case "active":
      return (
        <span className={`${base} border-2 border-signal-amber`}>
          <span className="size-3.5 rounded-full bg-signal-amber" aria-hidden />
          <span className="sr-only">In progress</span>
        </span>
      );
    case "blocked":
      return (
        <span className={`${base} bg-signal-red text-ink-950`}>
          <X weight="bold" className="size-5" aria-hidden />
          <span className="sr-only">Blocked</span>
        </span>
      );
    case "needs_you":
      return (
        <span className={`${base} border-2 border-fg text-fg`}>
          <Hand weight="bold" className="size-5" aria-hidden />
          <span className="sr-only">Needs your decision</span>
        </span>
      );
    default:
      return (
        <span className={`${base} border-2 border-ink-600`}>
          <span className="sr-only">Not started</span>
        </span>
      );
  }
}

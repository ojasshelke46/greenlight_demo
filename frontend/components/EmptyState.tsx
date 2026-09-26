"use client";

import { Binoculars, GitMerge, ListChecks } from "@phosphor-icons/react";
import type { ChatRole } from "@/lib/state";
import { Orb } from "./Orb";

export function Hero({ userName }: { userName: string }) {
  return (
    <div className="flex flex-col items-center px-5 text-center">
      <Orb size={176} layoutId="orb" bloom />
      <h1 className="text-gradient mt-9 pb-1 font-display text-[2rem] font-semibold sm:text-[2.6rem] leading-[1.1] tracking-[-0.03em] md:text-[3.25rem]">
        Welcome back, {userName}
      </h1>
      <p className="mt-3 text-[1.1rem] text-fg-muted">Which repo should I make safe today?</p>
    </div>
  );
}

// Each card starts the chat with the agent that does it.
const SUGGESTIONS: { title: [string, string]; description: string; Icon: typeof GitMerge; agent: ChatRole }[] = [
  { title: ["Fix and ship", "with proof"], description: "A prover checks every fix", Icon: GitMerge, agent: "fixer" },
  { title: ["Sweep a fleet", "find the worst repo"], description: "Ranks repos by risk", Icon: Binoculars, agent: "scout" },
  { title: ["Set the rules", "draft a policy"], description: "Approvers and freezes", Icon: ListChecks, agent: "policy" },
];

export function Suggestions({ onPick }: { onPick: (agent: ChatRole) => void }) {
  return (
    <div className="mx-auto grid w-full max-w-3xl grid-cols-1 gap-3 px-5 sm:grid-cols-3">
      {SUGGESTIONS.map(({ title, description, Icon, agent }) => (
        <button
          key={title[0]}
          type="button"
          onClick={() => onPick(agent)}
          className="group flex items-center gap-3.5 rounded-[var(--radius-card)] border border-line bg-panel/90 p-3.5 text-left transition-[border-color,background-color] duration-150 ease-out hover:border-line-strong hover:bg-card active:scale-[0.99]"
        >
          <span className="grid size-11 shrink-0 place-items-center rounded-full border border-accent/25 bg-accent/10 text-accent">
            <Icon weight="bold" className="size-5" aria-hidden />
          </span>
          <span className="min-w-0">
            <span className="block font-display text-[0.92rem] font-semibold leading-tight text-fg">
              {title[0]}
              <br />
              <span className="text-accent">{title[1]}</span>
            </span>
            <span className="mt-1 block truncate text-[0.75rem] text-fg-subtle">{description}</span>
          </span>
        </button>
      ))}
    </div>
  );
}

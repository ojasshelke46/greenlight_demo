"use client";

import { GitMerge, GitPullRequest, ShieldCheck } from "@phosphor-icons/react";
import type { Mode } from "@/lib/state";
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

const SUGGESTIONS: { title: [string, string]; description: string; Icon: typeof ShieldCheck; mode: Mode | null }[] = [
  { title: ["Scan a repo", "for vulnerabilities"], description: "npm audit, checked on OSV", Icon: ShieldCheck, mode: null },
  { title: ["Fix and ship", "with your approval"], description: "Merges only on your hold", Icon: GitMerge, mode: "ship" },
  { title: ["Open a PR", "on any public repo"], description: "Forks when it has to", Icon: GitPullRequest, mode: "pr_only" },
];

export function Suggestions({ onPick }: { onPick: (mode: Mode | null) => void }) {
  return (
    <div className="mx-auto grid w-full max-w-3xl grid-cols-1 gap-3 px-5 sm:grid-cols-3">
      {SUGGESTIONS.map(({ title, description, Icon, mode }) => (
        <button
          key={title[0]}
          type="button"
          onClick={() => onPick(mode)}
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

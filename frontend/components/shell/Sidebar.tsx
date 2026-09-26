"use client";

import { Menu } from "@base-ui/react/menu";
import { ArrowRight, ClockCounterClockwise, DotsThree, MagnifyingGlass, NotePencil, SidebarSimple } from "@phosphor-icons/react";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Tip } from "@/components/Tip";
import { ROLE_LABEL, type RunSummary } from "@/lib/state";

type Props = {
  className?: string;
  runs: RunSummary[] | null;
  runsError: string | null;
  activeRunId: string | null;
  onSelect: (run: RunSummary) => void;
  onNew: () => void;
  onCollapse: () => void;
  onOpenPolicy: () => void;
  onOpenLedger: (id: string) => void;
};

const GROUPS = ["Today", "Yesterday", "3 days ago", "7 days ago", "Last 30 days", "Older"] as const;

function groupOf(iso: string, now: Date): (typeof GROUPS)[number] {
  const date = new Date(iso);
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days <= 3) return "3 days ago";
  if (days <= 7) return "7 days ago";
  if (days <= 30) return "Last 30 days";
  return "Older";
}

const STATUS_DOT: Record<string, { className: string; label: string }> = {
  running: { className: "bg-progress", label: "Running" },
  awaiting_approval: { className: "bg-progress", label: "Awaiting approval" },
  awaiting_input: { className: "bg-progress", label: "Waiting for your answer" },
  done: { className: "bg-accent", label: "Done" },
  error: { className: "bg-fail", label: "Failed" },
  cancelled: { className: "bg-fail", label: "Stopped" },
  resume_failed: { className: "bg-fail", label: "Resume failed" },
};

export function Sidebar({ className, runs, runsError, activeRunId, onSelect, onNew, onCollapse, onOpenPolicy, onOpenLedger }: Props) {
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    if (!runs) return null;
    const now = new Date();
    const q = query.trim().toLowerCase();
    const filtered = runs.filter((r) => r.repo.toLowerCase().includes(q) || ROLE_LABEL[r.role].toLowerCase().includes(q));
    const map = new Map<string, RunSummary[]>();
    for (const run of filtered) {
      const group = groupOf(run.createdAt, now);
      map.set(group, [...(map.get(group) ?? []), run]);
    }
    return GROUPS.filter((g) => map.has(g)).map((g) => ({ title: g, runs: map.get(g)! }));
  }, [runs, query]);

  return (
    <aside className={clsx("z-40 h-full w-[300px] shrink-0 flex-col border-r border-line bg-panel/95 backdrop-blur-sm max-lg:fixed max-lg:inset-y-0 max-lg:left-0 max-lg:shadow-2xl lg:relative", className)} aria-label="Runs">
      <div className="flex h-16 items-center gap-1 px-4">
        <Tip label="New run">
          <button type="button" onClick={onNew} aria-label="New run" className="grid size-9 place-items-center rounded-full text-fg-muted transition-colors duration-150 hover:bg-card hover:text-fg">
            <NotePencil weight="bold" className="size-[18px]" aria-hidden />
          </button>
        </Tip>
        <Tip label="Raw event log">
          <a href="/raw" aria-label="Raw event log" className="grid size-9 place-items-center rounded-full text-fg-muted transition-colors duration-150 hover:bg-card hover:text-fg">
            <ClockCounterClockwise weight="bold" className="size-[18px]" aria-hidden />
          </a>
        </Tip>
        <Tip label="Hide sidebar">
          <button type="button" onClick={onCollapse} aria-label="Hide sidebar" className="ml-auto grid size-9 place-items-center rounded-full text-fg-muted transition-colors duration-150 hover:bg-card hover:text-fg">
            <SidebarSimple weight="bold" className="size-[18px]" aria-hidden />
          </button>
        </Tip>
      </div>

      <div className="px-4">
        <label className="flex h-11 items-center gap-2 rounded-full border border-line bg-card px-4 transition-colors duration-150 focus-within:border-line-strong">
          <MagnifyingGlass weight="bold" className="size-4 text-fg-subtle" aria-hidden />
          <span className="sr-only">Search runs</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search runs"
            className="min-w-0 flex-1 bg-transparent text-[0.88rem] text-fg outline-none placeholder:text-fg-subtle"
          />
        </label>
      </div>

      <nav className="mt-4 min-h-0 flex-1 overflow-y-auto px-4 pb-4">
        {groups === null && !runsError && (
          <div className="flex flex-col gap-2" aria-label="Loading runs">
            <div className="h-4 w-16 animate-pulse rounded-full bg-card motion-reduce:animate-none" />
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-10 animate-pulse rounded-full bg-card motion-reduce:animate-none" />
            ))}
          </div>
        )}
        {runsError && <p className="px-2 text-[0.85rem] text-fg-subtle">Could not load runs. {runsError}</p>}
        {groups && groups.length === 0 && (
          <p className="px-2 text-[0.85rem] leading-relaxed text-fg-subtle">
            {query ? "No runs match that search." : "No runs yet. Paste a repo link to start one."}
          </p>
        )}
        {groups?.map((group) => (
          <section key={group.title} className="mb-5">
            <h2 className="mb-2 px-1 text-[0.85rem] font-semibold text-fg">{group.title}</h2>
            <ul className="flex flex-col gap-1.5">
              {group.runs.map((run) => {
                const dot = STATUS_DOT[run.status] ?? { className: "bg-fg-subtle", label: run.status };
                const active = run.id === activeRunId;
                return (
                  <li key={run.id} className={clsx("group flex h-10 items-center rounded-full border pl-3.5 pr-1 transition-colors duration-150", active ? "border-accent/30 bg-card" : "border-transparent bg-card/50 hover:bg-card")}>
                    <button type="button" onClick={() => onSelect(run)} className="flex min-w-0 flex-1 items-center gap-2.5 text-left" aria-current={active ? "page" : undefined}>
                      <span className={clsx("size-2 shrink-0 rounded-full", dot.className)} role="img" aria-label={dot.label} />
                      <span className="min-w-0 flex-1 truncate font-mono text-[0.8rem] text-fg-muted group-hover:text-fg">{run.repo.replace(/^repos:/, "")}</span>
                      <span className="shrink-0 rounded-full border border-line px-1.5 py-px text-[0.72rem] font-medium text-fg-subtle">{ROLE_LABEL[run.role]}</span>
                    </button>
                    <RunMenu runId={run.id} onOpen={() => onSelect(run)} onLedger={() => onOpenLedger(run.id)} />
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </nav>

      <div className="p-4">
        <div className="policy-card relative overflow-hidden rounded-[var(--radius-card)] border border-accent/25 p-4">
          <p className="pr-10 font-display text-[0.95rem] font-semibold leading-snug text-fg">
            Nothing merges <span className="text-accent">without your approval</span>
          </p>
          <p className="mt-1.5 pr-10 text-[0.78rem] leading-snug text-fg-muted">See what the agent may do on its own.</p>
          <button
            type="button"
            onClick={onOpenPolicy}
            aria-label="Open the policy"
            className="absolute bottom-4 right-4 grid size-8 place-items-center rounded-full bg-fg text-canvas transition-transform duration-150 ease-out hover:scale-105 active:scale-95"
          >
            <ArrowRight weight="bold" className="size-4" aria-hidden />
          </button>
        </div>
      </div>
    </aside>
  );
}

// Menus open tens of times a day: 150ms, strong ease out, scaling from the trigger.
function RunMenu({ runId, onOpen, onLedger }: { runId: string; onOpen: () => void; onLedger: () => void }) {
  const item = "flex cursor-default items-center rounded-lg px-3 py-2 text-[0.85rem] text-fg outline-none select-none data-highlighted:bg-card";
  return (
    <Menu.Root>
      <Menu.Trigger aria-label="Run actions" className="grid size-8 shrink-0 place-items-center rounded-full text-fg-subtle transition-colors duration-150 hover:bg-raised hover:text-fg data-popup-open:bg-raised">
        <DotsThree weight="bold" className="size-5" aria-hidden />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={6} align="end" className="z-50 outline-none">
          <Menu.Popup className="min-w-48 origin-[var(--transform-origin)] rounded-xl border border-line-strong bg-raised p-1 shadow-2xl outline-none transition-[transform,opacity] duration-150 ease-[var(--ease-out)] data-ending-style:scale-[0.97] data-ending-style:opacity-0 data-starting-style:scale-[0.97] data-starting-style:opacity-0">
            <Menu.Item className={item} onClick={onOpen}>
              Open run
            </Menu.Item>
            <Menu.Item className={item} onClick={onLedger}>
              Open audit ledger
            </Menu.Item>
            <Menu.Item className={item} onClick={() => navigator.clipboard.writeText(runId).catch(() => {})}>
              Copy run id
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}

"use client";

import { Broom, Stop, Warning } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState, type FormEvent } from "react";
import { FleetRow, SeverityChips } from "./FleetRow";
import { exposed, SEVERITIES, useSweep, type Counts, type Phase, type RepoResult, type Summary } from "./sweep";

function liveTotals(rows: RepoResult[], summary: Summary | null): { scanned: number; exposed: number; counts: Counts } {
  if (summary) return { scanned: summary.totals.repos, exposed: summary.repos.filter(exposed).length, counts: summary.totals.counts };
  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, 0])) as Counts;
  for (const row of rows) for (const s of SEVERITIES) counts[s] += row.counts[s];
  return { scanned: rows.length, exposed: rows.filter(exposed).length, counts };
}

const PHASE_TEXT: Record<Exclude<Phase, "idle">, string> = {
  sweeping: "Sweeping",
  done: "Done",
  stopped: "Stopped",
  failed: "Stopped early",
};

export function FleetView() {
  const { state, sweep, stop } = useSweep();
  const [target, setTarget] = useState("");
  const sweeping = state.phase === "sweeping";
  const totals = liveTotals(state.rows, state.summary);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = target.trim();
    if (!value || sweeping) return;
    void sweep(value);
  };

  return (
    <div className="flex flex-col gap-8">
      <header className="max-w-[72ch]">
        <h1 className="font-display text-[2rem] font-semibold leading-tight tracking-tight text-fg md:text-[2.4rem]">Sweep a fleet</h1>
        <p className="mt-2 text-[0.95rem] leading-relaxed text-fg-muted">
          Check every repo&apos;s lockfile against OSV. Read only: nothing runs and nothing merges.
        </p>
      </header>

      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor="fleet-target" className="text-[0.82rem] font-medium text-fg-muted">
          Org, user or repos
        </label>
        <div className="composer flex items-center gap-3 rounded-[var(--radius-composer)] border border-line bg-[#090909]/95 p-2.5 pl-5 shadow-[0_24px_60px_-20px_rgb(0_0_0/0.8)] max-sm:flex-col max-sm:items-stretch max-sm:pl-2.5">
          <input
            id="fleet-target"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder="user:ojasshelke46"
            spellCheck={false}
            autoComplete="off"
            aria-describedby="fleet-target-help"
            className="h-12 min-w-0 flex-1 bg-transparent font-mono text-[1.05rem] text-fg outline-none placeholder:text-fg-subtle max-sm:px-2.5"
          />
          {sweeping ? (
            <button
              type="button"
              onClick={stop}
              className="inline-flex h-12 shrink-0 items-center justify-center gap-2 rounded-full border border-line-strong bg-card px-6 font-display text-[0.95rem] font-semibold text-fg transition-[background-color,transform] duration-150 ease-out hover:bg-raised active:scale-[0.97]"
            >
              <Stop weight="fill" className="size-4" aria-hidden />
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!target.trim()}
              className="inline-flex h-12 shrink-0 items-center justify-center gap-2 rounded-full bg-accent px-6 font-display text-[0.95rem] font-semibold text-canvas shadow-[0_0_24px_-6px_color-mix(in_oklab,var(--accent)_60%,transparent)] transition-[opacity,transform] duration-150 ease-out active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
            >
              <Broom weight="bold" className="size-4" aria-hidden />
              Sweep
            </button>
          )}
        </div>
        <p id="fleet-target-help" className="text-[0.8rem] text-fg-subtle">
          <span className="font-mono">org:name</span>, <span className="font-mono">user:name</span>, or repo links separated by commas. Archived repos and forks are skipped.
        </p>
      </form>

      {state.phase === "failed" && state.error && (
        <p role="alert" className="flex items-start gap-2 rounded-xl border border-fail/30 bg-fail/10 px-4 py-3 text-[0.9rem] text-fail">
          <Warning weight="bold" className="mt-0.5 size-4 shrink-0" aria-hidden />
          {state.error}
        </p>
      )}

      {state.phase === "idle" ? (
        <p className="rounded-[var(--radius-card)] border border-dashed border-line-strong px-5 py-8 text-center text-[0.9rem] text-fg-muted">
          Results land here one repo at a time, riskiest first.
        </p>
      ) : (
        <section aria-label="Sweep results" className="flex flex-col gap-3">
          <SummaryBar phase={state.phase} target={state.target} {...totals} />

          {state.rows.length === 0 && sweeping ? (
            <ul className="flex flex-col gap-2" aria-label="Waiting for the first repo">
              {[0, 1, 2].map((i) => (
                <li key={i} className="flex h-[74px] flex-col justify-center gap-2 rounded-[var(--radius-card)] border border-line bg-panel/60 px-4">
                  <div className="h-3.5 w-1/3 animate-pulse rounded-full bg-card motion-reduce:animate-none" />
                  <div className="h-3 w-1/2 animate-pulse rounded-full bg-card motion-reduce:animate-none" />
                </li>
              ))}
            </ul>
          ) : state.rows.length === 0 && state.phase === "done" ? (
            <p className="rounded-[var(--radius-card)] border border-line bg-panel/60 px-5 py-6 text-[0.9rem] text-fg-muted">
              No repos to sweep for this target. It may only have archived repos or forks.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {state.rows.map((row) => (
                // Keyed by repo: a row enters once when it mounts, then only moves as the order changes.
                <FleetRow key={row.repo} row={row} />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

function SummaryBar({ phase, target, scanned, exposed: exposedCount, counts }: { phase: Phase; target: string | null; scanned: number; exposed: number; counts: Counts }) {
  if (phase === "idle") return null;
  return (
    <div className="flex flex-wrap items-center gap-x-8 gap-y-3 rounded-[var(--radius-card)] border border-line bg-panel/80 px-5 py-4">
      <Stat label="Repos scanned" value={scanned} />
      <Stat label="Repos exposed" value={exposedCount} className={exposedCount > 0 ? "text-fail" : "text-fg"} />
      <SeverityChips counts={counts} className="md:flex-1" />
      <p className="flex items-center gap-2 text-[0.82rem] text-fg-muted md:ml-auto" aria-live="polite">
        {phase === "sweeping" && <span aria-hidden className="dot-pulse size-2 rounded-full bg-progress shadow-[0_0_10px_rgb(255_176_32/0.6)]" />}
        <span className={clsx(phase === "done" && "text-accent", phase === "failed" && "text-fail")}>{PHASE_TEXT[phase]}</span>
        {target && <span className="max-w-[28ch] truncate font-mono text-fg-subtle">{target}</span>}
      </p>
    </div>
  );
}

function Stat({ label, value, className }: { label: string; value: number; className?: string }) {
  return (
    <div className="flex flex-col">
      <span className={clsx("font-mono text-[1.6rem] leading-none tabular-nums", className ?? "text-fg")}>{value}</span>
      <span className="mt-1.5 text-[0.78rem] text-fg-subtle">{label}</span>
    </div>
  );
}

"use client";

import { CaretDown, Wrench } from "@phosphor-icons/react";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useState } from "react";
import { exposed, SEVERITIES, type Advisory, type Counts, type RepoResult, type Severity } from "./sweep";

// A row arriving: occasional, 220ms strong ease out, a short lift, like blocks in the conversation.
const ENTER = { duration: 0.22, ease: [0.23, 1, 0.32, 1] } as const;
// Rows reordering by risk move on screen: strong ease in out, 350ms (DESIGN.md).
const GLIDE = { duration: 0.35, ease: [0.77, 0, 0.175, 1] } as const;

const CHIP: Record<Severity, string> = {
  critical: "border-fail/40 bg-fail/12 text-fail",
  high: "border-fail/40 bg-fail/12 text-fail",
  moderate: "border-progress/40 bg-progress/12 text-progress",
  low: "border-line-strong bg-card text-fg-muted",
  unknown: "border-line bg-card text-fg-subtle",
};

const CHIP_LABEL: Record<Severity, string> = { critical: "critical", high: "high", moderate: "moderate", low: "low", unknown: "unrated" };

const GHSA = /^GHSA(-[a-z0-9]{4}){3}$/i;

export function SeverityChips({ counts, className }: { counts: Counts; className?: string }) {
  const present = SEVERITIES.filter((s) => counts[s] > 0);
  if (present.length === 0) return null;
  return (
    <ul className={clsx("flex flex-wrap gap-1.5", className)} aria-label="Advisories by severity">
      {present.map((severity) => (
        <li key={severity} className={clsx("inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-[0.78rem] font-medium", CHIP[severity])}>
          <span className="font-mono tabular-nums">{counts[severity]}</span> {CHIP_LABEL[severity]}
        </li>
      ))}
    </ul>
  );
}

function AdvisoryId({ id }: { id: string }) {
  return GHSA.test(id) ? (
    <a href={`https://github.com/advisories/${id}`} target="_blank" rel="noreferrer" className="font-mono text-fg underline decoration-line-strong underline-offset-4 transition-colors duration-150 hover:decoration-fg-muted">
      {id}
    </a>
  ) : (
    <span className="font-mono text-fg">{id}</span>
  );
}

function Finding({ advisory }: { advisory: Advisory }) {
  return (
    <>
      <AdvisoryId id={advisory.id} /> in{" "}
      <span className="font-mono text-fg">
        {advisory.package} {advisory.version}
      </span>
      {advisory.summary && <span className="text-fg-muted">: {advisory.summary}</span>}
    </>
  );
}

function Detail({ row }: { row: RepoResult }) {
  if (row.status === "no_lockfile") {
    return (
      <p className="text-[0.85rem] text-fg-muted">
        No package-lock.json on <span className="font-mono">{row.default_branch ?? "the default branch"}</span>
      </p>
    );
  }
  if (row.status === "error") return <p className="text-[0.85rem] text-fail">{row.error ?? "Could not scan this repo"}</p>;
  if (!exposed(row)) {
    return (
      <p className="text-[0.85rem] text-accent">
        No known advisories in {row.packages} {row.packages === 1 ? "package" : "packages"}
      </p>
    );
  }
  return (
    <p className="truncate text-[0.85rem] text-fg-subtle">
      <Finding advisory={row.top[0]} />
    </p>
  );
}

/** onFix starts a Fix run in place (the chat); without it the row links to the launch view with ?repo=. */
export function FleetRow({ row, onFix }: { row: RepoResult; onFix?: (repo: string) => void }) {
  const reduce = useReducedMotion();
  const [open, setOpen] = useState(false);
  const canOpen = row.top.length > 0;
  const hot = exposed(row);

  return (
    <motion.li
      layout={reduce ? false : "position"}
      initial={reduce ? { opacity: 0 } : { opacity: 0, transform: "translateY(6px)" }}
      animate={{ opacity: 1, transform: "translateY(0px)" }}
      transition={{ ...ENTER, layout: GLIDE }}
      className="overflow-hidden rounded-[var(--radius-card)] border border-line bg-panel/80"
    >
      <div className="grid grid-cols-1 gap-3 px-4 py-3.5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:gap-6">
        <div className="min-w-0">
          <p className="flex min-w-0 items-baseline gap-2">
            <a
              href={`https://github.com/${row.repo}`}
              target="_blank"
              rel="noreferrer"
              className="truncate font-mono text-[0.92rem] text-fg transition-colors duration-150 hover:text-accent"
            >
              {row.repo}
            </a>
            {row.default_branch && <span className="shrink-0 font-mono text-[0.75rem] text-fg-subtle">{row.default_branch}</span>}
          </p>
          <div className="mt-1">
            <Detail row={row} />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 md:justify-end">
          <SeverityChips counts={row.counts} />
          {hot && onFix && (
            <button
              type="button"
              onClick={() => onFix(row.repo)}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-accent/45 bg-accent/[0.07] px-3 text-[0.82rem] font-medium text-accent transition-[background-color,transform] duration-150 ease-out hover:bg-accent/15 active:scale-[0.97]"
            >
              <Wrench weight="bold" className="size-3.5" aria-hidden />
              Fix this repo
            </button>
          )}
          {hot && !onFix && (
            <Link
              href={`/?repo=${encodeURIComponent(`https://github.com/${row.repo}`)}`}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-accent/45 bg-accent/[0.07] px-3 text-[0.82rem] font-medium text-accent transition-[background-color,transform] duration-150 ease-out hover:bg-accent/15 active:scale-[0.97]"
            >
              <Wrench weight="bold" className="size-3.5" aria-hidden />
              Fix this repo
            </Link>
          )}
          {canOpen && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              aria-label={open ? `Hide advisories for ${row.repo}` : `Show top advisories for ${row.repo}`}
              className="grid size-8 place-items-center rounded-full text-fg-subtle transition-[color,background-color,transform] duration-150 ease-out hover:bg-card hover:text-fg active:scale-[0.94]"
            >
              <CaretDown weight="bold" className={clsx("size-4 transition-transform duration-200 ease-[var(--ease-out)]", open && "rotate-180")} aria-hidden />
            </button>
          )}
        </div>
      </div>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="advisories"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={reduce ? { duration: 0.15, height: { duration: 0 } } : ENTER}
            className="overflow-hidden"
          >
            <ol className="flex flex-col gap-2 border-t border-line px-4 pb-4 pt-3" aria-label={`Top advisories for ${row.repo}`}>
              {row.top.map((advisory) => (
                <li key={advisory.id} className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-3 text-[0.85rem] leading-relaxed">
                  <span className={clsx("inline-flex h-6 items-center rounded-full border px-2 text-[0.72rem] font-medium", CHIP[advisory.severity])}>{CHIP_LABEL[advisory.severity]}</span>
                  <span className="min-w-0 break-words text-fg-subtle">
                    <Finding advisory={advisory} />
                  </span>
                </li>
              ))}
            </ol>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  );
}

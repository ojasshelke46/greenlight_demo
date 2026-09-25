"use client";

import { Bug, GitDiff, TerminalWindow } from "@phosphor-icons/react";
import { useEffect, useId, useRef, useState } from "react";
import type { DiffFile, RunState, TerminalLine, Vulnerability } from "@/lib/state";

type Tab = "terminal" | "vulnerabilities" | "diff";

export function OutputPanel({ run }: { run: RunState }) {
  const [tab, setTab] = useState<Tab>("terminal");
  const id = useId();

  const tabs: { id: Tab; label: string; count?: number; Icon: typeof Bug }[] = [
    { id: "terminal", label: "Terminal", Icon: TerminalWindow },
    { id: "vulnerabilities", label: "Vulnerabilities", count: run.vulnerabilities.length, Icon: Bug },
    { id: "diff", label: "Code diff", count: run.diff.length, Icon: GitDiff },
  ];

  return (
    <section className="flex min-h-[28rem] flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-900 lg:min-h-0">
      <div role="tablist" aria-label="Run output" className="flex shrink-0 gap-1 overflow-x-auto border-b border-ink-700 px-3 pt-2">
        {tabs.map(({ id: tabId, label, count, Icon }) => {
          const selected = tab === tabId;
          return (
            <button
              key={tabId}
              type="button"
              role="tab"
              id={`${id}-${tabId}-tab`}
              aria-selected={selected}
              aria-controls={`${id}-${tabId}-panel`}
              onClick={() => setTab(tabId)}
              className={[
                "-mb-px flex shrink-0 items-center gap-2 whitespace-nowrap rounded-t-xl border-b-2 px-4 py-3 text-base font-medium transition-colors",
                selected ? "border-fg text-fg" : "border-transparent text-fg-muted hover:text-fg",
              ].join(" ")}
            >
              <Icon weight="bold" className="size-5" aria-hidden />
              {label}
              {count !== undefined && <span className="font-mono text-fg-muted">{count}</span>}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`${id}-${tab}-panel`}
        aria-labelledby={`${id}-${tab}-tab`}
        className="flex min-h-0 flex-1 flex-col"
      >
        {tab === "terminal" && <Terminal lines={run.terminal} running={run.status.signal === "amber"} />}
        {tab === "vulnerabilities" && <Vulnerabilities items={run.vulnerabilities} />}
        {tab === "diff" && <Diff files={run.diff} />}
      </div>
    </section>
  );
}

const LINE_STYLE: Record<TerminalLine["kind"], string> = {
  command: "text-fg",
  output: "text-fg-muted",
  pass: "text-signal-green",
  fail: "text-signal-red",
  agent: "text-fg",
};

function Terminal({ lines, running }: { lines: TerminalLine[]; running: boolean }) {
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  if (lines.length === 0) {
    return <EmptyState>Waiting for the sandbox to start. Its output streams here.</EmptyState>;
  }

  return (
    <div
      ref={scroller}
      className="min-h-0 flex-1 overflow-y-auto bg-ink-950 px-6 py-5 font-mono text-[0.95rem] leading-relaxed"
      aria-live="polite"
      aria-relevant="additions"
    >
      {lines.map((line) => (
        <div key={line.id} className={`whitespace-pre-wrap break-words ${LINE_STYLE[line.kind]}`}>
          {line.kind === "command" && <span className="select-none text-fg-subtle">$ </span>}
          {line.kind === "agent" && <span className="select-none text-fg-subtle">greenlight › </span>}
          {line.text}
        </div>
      ))}
      {running && <span className="terminal-cursor mt-1" aria-hidden />}
    </div>
  );
}

const VULN_STATUS: Record<Vulnerability["status"], { label: string; className: string }> = {
  open: { label: "Vulnerable", className: "text-signal-red" },
  fixing: { label: "Agent fixing", className: "text-signal-amber" },
  fixed: { label: "Fixed", className: "text-fg" },
};

function Vulnerabilities({ items }: { items: Vulnerability[] }) {
  if (items.length === 0) {
    return <EmptyState>No vulnerable packages found yet. The scan reports them here.</EmptyState>;
  }
  return (
    <ul className="min-h-0 flex-1 divide-y divide-ink-700 overflow-y-auto">
      {items.map((v) => (
        <li key={v.advisory} className="grid gap-4 px-6 py-6 md:grid-cols-[minmax(0,1fr)_auto]">
          <div className="min-w-0">
            <p className="font-mono text-2xl font-semibold">{v.packageName}</p>
            <p className="mt-2 max-w-[60ch] text-lg leading-relaxed text-fg-muted">{v.summary}</p>
            <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-base">
              <Fact label="Installed" value={v.installed} />
              <Fact label="Patched in" value={v.patched} />
              <Fact label="Severity" value={v.severity} />
              <Fact label="Advisory" value={v.advisory} />
            </dl>
          </div>
          <p className={`text-lg font-semibold ${VULN_STATUS[v.status].className}`}>{VULN_STATUS[v.status].label}</p>
        </li>
      ))}
    </ul>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  );
}

function Diff({ files }: { files: DiffFile[] }) {
  if (files.length === 0) {
    return <EmptyState>No changes yet. The diff appears once Greenlight edits a file.</EmptyState>;
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-ink-950 py-4 font-mono text-[0.95rem] leading-relaxed">
      {files.map((file) => (
        <section key={file.path} className="mb-6 last:mb-0">
          <h3 className="px-6 pb-2 text-base font-semibold text-fg">{file.path}</h3>
          {file.hunks.map((hunk) => (
            <div key={hunk.header} className="mb-3">
              <p className="px-6 text-fg-subtle">{hunk.header}</p>
              {hunk.lines.map((line, index) => (
                <div
                  key={index}
                  className={[
                    "flex whitespace-pre-wrap px-6",
                    line.kind === "add" ? "bg-fg/[0.07] text-fg" : "",
                    line.kind === "remove" ? "text-fg-subtle" : "",
                    line.kind === "context" ? "text-fg-muted" : "",
                  ].join(" ")}
                >
                  <span className="w-6 shrink-0 select-none" aria-hidden>
                    {line.kind === "add" ? "+" : line.kind === "remove" ? "−" : " "}
                  </span>
                  <span className="sr-only">{line.kind === "add" ? "Added: " : line.kind === "remove" ? "Removed: " : ""}</span>
                  <span className="min-w-0 break-words">{line.text || " "}</span>
                </div>
              ))}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return <p className="m-auto max-w-[36ch] px-6 py-16 text-center text-lg text-fg-muted">{children}</p>;
}

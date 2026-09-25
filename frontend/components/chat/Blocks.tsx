"use client";

import {
  ArrowSquareOut,
  CaretDown,
  Check,
  CircleNotch,
  Copy,
  GitBranch,
  GitPullRequest,
  ShieldWarning,
  TerminalWindow,
  Wrench,
  X,
} from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Block, DiffFile, PullRequest, Step, TerminalCommand, Tone, Vulnerability } from "@/lib/state";

const TONE_TEXT: Record<Tone, string> = { fail: "text-fail", progress: "text-progress", done: "text-accent" };

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

function elapsed(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

// Accordion: height is the one tolerated layout animation. Grid rows keep it off JS; 240ms ease out.
function Collapse({ open, children }: { open: boolean; children: React.ReactNode }) {
  return (
    <div className={clsx("grid transition-[grid-template-rows] duration-[240ms] ease-[var(--ease-out)]", open ? "grid-rows-[1fr]" : "grid-rows-[0fr]")}>
      <div className="min-h-0 overflow-hidden">{children}</div>
    </div>
  );
}

export function StepRail({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState(true);
  const anyActive = steps.some((s) => s.status === "active");
  const now = useNow(anyActive);
  const done = steps.filter((s) => s.status === "done").length;

  return (
    <section className="rounded-[var(--radius-card)] border border-line bg-panel/80">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left"
      >
        <span className="text-[0.9rem] font-semibold text-fg">Steps</span>
        <span className="font-mono text-[0.78rem] text-fg-subtle">
          {done} of {steps.length}
        </span>
        <CaretDown weight="bold" className={clsx("ml-auto size-4 text-fg-subtle transition-transform duration-200 ease-[var(--ease-out)]", !open && "-rotate-90")} aria-hidden />
      </button>
      <Collapse open={open}>
        <ol className="flex flex-col gap-0.5 px-4 pb-3" aria-label="Run steps">
          {steps.map((step) => {
            const time = step.startedAt ? (step.endedAt ?? (step.status === "active" ? now : undefined)) : undefined;
            return (
              <li key={step.id} className="grid grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-3 py-1.5" aria-current={step.status === "active" ? "step" : undefined}>
                <StepDot status={step.status} />
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className={clsx("shrink-0 text-[0.88rem]", step.status === "waiting" ? "text-fg-subtle" : "text-fg")}>{step.label}</span>
                  {step.detail && (
                    <span className={clsx("truncate text-[0.82rem]", step.tone ? TONE_TEXT[step.tone] : "text-fg-muted")}>{step.detail}</span>
                  )}
                </span>
                <span className="font-mono text-[0.75rem] tabular-nums text-fg-subtle">
                  {time !== undefined && step.startedAt ? elapsed(time - step.startedAt) : ""}
                </span>
              </li>
            );
          })}
        </ol>
      </Collapse>
    </section>
  );
}

function StepDot({ status }: { status: Step["status"] }) {
  const label = { waiting: "Not started", active: "In progress", done: "Done", failed: "Failed", paused: "Paused" }[status];
  return (
    <span className="relative grid size-3.5 place-items-center" role="img" aria-label={label}>
      <span
        className={clsx(
          "size-2.5 rounded-full transition-[background-color,box-shadow] duration-200 ease-out",
          status === "waiting" && "border border-line-strong",
          status === "active" && "dot-pulse bg-progress shadow-[0_0_10px_rgb(255_176_32/0.6)]",
          status === "done" && "bg-accent shadow-[0_0_10px_color-mix(in_oklab,var(--accent)_55%,transparent)]",
          status === "failed" && "bg-fail",
          status === "paused" && "bg-progress",
        )}
      />
    </span>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        } catch {}
      }}
      aria-label={copied ? "Copied" : label}
      className="grid size-8 place-items-center rounded-full text-fg-subtle transition-colors duration-150 hover:bg-card hover:text-fg"
    >
      {copied ? <Check weight="bold" className="size-4 text-accent" aria-hidden /> : <Copy weight="bold" className="size-4" aria-hidden />}
    </button>
  );
}

export function TerminalBlock({ commands }: { commands: TerminalCommand[] }) {
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const current = commands.find((c) => c.running) ?? commands[commands.length - 1];
  const lineCount = commands.reduce((n, c) => n + c.lines.length + 1, 0);

  // Follow new output unless the reader scrolled up to look at something.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lineCount]);

  const transcript = commands.map((c) => [`$ ${c.command}`, ...c.lines.map((l) => l.text)].join("\n")).join("\n");

  return (
    <section className="overflow-hidden rounded-[var(--radius-card)] border border-line bg-[#070707]">
      <header className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <TerminalWindow weight="bold" className="size-4 shrink-0 text-fg-subtle" aria-hidden />
        <p className="min-w-0 flex-1 truncate font-mono text-[0.8rem] text-fg" title={current?.command}>
          {current?.intent ?? current?.command}
        </p>
        {current?.running ? (
          <span className="flex items-center gap-1.5 text-[0.78rem] text-progress">
            <CircleNotch weight="bold" className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
            Running
          </span>
        ) : current?.stopped ? (
          <span className="text-[0.78rem] text-fg-subtle">Stopped</span>
        ) : current?.ok === false ? (
          <span className="text-[0.78rem] text-fail">{current.exitCode !== null ? `Exit ${current.exitCode}` : "Failed"}</span>
        ) : (
          <span className="text-[0.78rem] text-accent">Done</span>
        )}
        <CopyButton text={transcript} label="Copy terminal output" />
      </header>
      <div
        ref={scroller}
        onScroll={(event) => {
          const el = event.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="max-h-80 overflow-y-auto px-4 py-3 font-mono text-[0.8rem] leading-relaxed"
        aria-live="off"
      >
        {commands.map((c) => (
          <div key={c.id} className="mb-2 last:mb-0">
            <div className="term-line whitespace-pre-wrap break-words text-fg">
              <span className="select-none text-accent">$ </span>
              {c.command}
            </div>
            {c.lines.map((line) => (
              <div
                key={line.id}
                className={clsx(
                  "term-line whitespace-pre-wrap break-words",
                  line.kind === "pass" && "text-accent",
                  line.kind === "fail" && "text-fail",
                  line.kind === "output" && "text-fg-muted",
                )}
              >
                {line.text || " "}
              </div>
            ))}
            {c.ok === false && c.exitCode !== null && <div className="term-line text-fail">exit code {c.exitCode}</div>}
            {c.stopped && <div className="term-line text-fg-subtle">stopped before it returned</div>}
          </div>
        ))}
      </div>
    </section>
  );
}

const SEVERITY_TONE: Record<string, string> = {
  critical: "border-fail/40 bg-fail/12 text-fail",
  high: "border-fail/40 bg-fail/12 text-fail",
  moderate: "border-progress/40 bg-progress/12 text-progress",
  medium: "border-progress/40 bg-progress/12 text-progress",
};

const VULN_STATUS: Record<Vulnerability["status"], { label: string; className: string }> = {
  open: { label: "Vulnerable", className: "text-fail" },
  fixing: { label: "Fixing", className: "text-progress" },
  fixed: { label: "Fixed", className: "text-accent" },
};

export function VulnBlock({ items }: { items: Vulnerability[] }) {
  return (
    <section className="grid gap-2" aria-label="Vulnerabilities">
      {items.map((v) => (
        <article key={`${v.packageName}:${v.advisory}`} className="flex items-start gap-4 rounded-[var(--radius-card)] border border-line bg-card px-4 py-3.5">
          <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-full bg-fail/12 text-fail">
            <ShieldWarning weight="bold" className="size-5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <p className="font-mono text-[0.95rem] font-semibold text-fg">{v.packageName}</p>
              {v.severity && (
                <span className={clsx("rounded-full border px-2 py-0.5 text-[0.7rem] font-semibold uppercase tracking-wide", SEVERITY_TONE[v.severity.toLowerCase()] ?? "border-line-strong text-fg-muted")}>
                  {v.severity}
                </span>
              )}
              <span className={clsx("ml-auto text-[0.8rem] font-medium", VULN_STATUS[v.status].className)}>{VULN_STATUS[v.status].label}</span>
            </div>
            {v.summary && <p className="mt-1 text-[0.85rem] leading-snug text-fg-muted">{v.summary}</p>}
            <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[0.78rem]">
              {(v.affected || v.patched) && (
                <span className="text-fg-muted">
                  {v.affected && <span className="text-fail">{v.affected}</span>}
                  {/^\d/.test(v.patched) ? (
                    <>
                      <span className="px-1.5 text-fg-subtle">to</span>
                      <span className="text-accent">{v.patched}</span>
                    </>
                  ) : (
                    v.patched && <span className="pl-2 text-fg-subtle">{v.patched}</span>
                  )}
                </span>
              )}
              {v.url ? (
                <a href={v.url} target="_blank" rel="noreferrer" className="text-fg-subtle underline decoration-line-strong underline-offset-4 hover:text-fg">
                  {v.advisory}
                </a>
              ) : (
                <span className="text-fg-subtle">{v.advisory}</span>
              )}
            </p>
          </div>
        </article>
      ))}
    </section>
  );
}

type Token = { content: string; color?: string };

const LANG_BY_EXT: Record<string, string> = { js: "javascript", cjs: "javascript", mjs: "javascript", ts: "typescript", tsx: "tsx", jsx: "jsx", json: "json", py: "python", md: "markdown", yml: "yaml", yaml: "yaml", sh: "shellscript", css: "css", html: "html" };

function useHighlighted(file: DiffFile): Token[][] | null {
  const [tokens, setTokens] = useState<Token[][] | null>(null);
  const source = file.hunks.flatMap((h) => h.lines.map((l) => l.text)).join("\n");
  const lang = LANG_BY_EXT[file.path.split(".").pop()?.toLowerCase() ?? ""];
  useEffect(() => {
    if (!lang) return;
    let cancelled = false;
    import("shiki/bundle/web")
      .then(({ codeToTokensBase }) => codeToTokensBase(source, { lang: lang as never, theme: "vesper" }))
      .then((result) => !cancelled && setTokens(result))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [source, lang]);
  return tokens;
}

function DiffFileView({ file }: { file: DiffFile }) {
  const [open, setOpen] = useState(true);
  const tokens = useHighlighted(file);
  const adds = file.hunks.reduce((n, h) => n + h.lines.filter((l) => l.kind === "add").length, 0);
  const removes = file.hunks.reduce((n, h) => n + h.lines.filter((l) => l.kind === "remove").length, 0);
  let row = 0;

  return (
    <div className="border-b border-line last:border-b-0">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-3 px-4 py-2.5 text-left">
        <CaretDown weight="bold" className={clsx("size-3.5 text-fg-subtle transition-transform duration-200 ease-[var(--ease-out)]", !open && "-rotate-90")} aria-hidden />
        <span className="min-w-0 flex-1 truncate font-mono text-[0.82rem] text-fg">{file.path}</span>
        <span className="font-mono text-[0.75rem] text-accent">+{adds}</span>
        <span className="font-mono text-[0.75rem] text-fail">-{removes}</span>
      </button>
      <Collapse open={open}>
        <div className="overflow-x-auto pb-2 font-mono text-[0.78rem] leading-relaxed">
          {file.hunks.map((hunk, h) => (
            <div key={`${hunk.header}:${h}`}>
              <p className="px-4 py-0.5 text-fg-subtle">{hunk.header}</p>
              {hunk.lines.map((line, i) => {
                const lineTokens = tokens?.[row++];
                return (
                  <div
                    key={i}
                    className={clsx(
                      "flex whitespace-pre px-4",
                      line.kind === "add" && "bg-[color-mix(in_oklab,var(--accent)_9%,transparent)]",
                      line.kind === "remove" && "bg-fail/[0.08]",
                    )}
                  >
                    <span className={clsx("w-5 shrink-0 select-none", line.kind === "add" ? "text-accent" : line.kind === "remove" ? "text-fail" : "text-fg-subtle")} aria-hidden>
                      {line.kind === "add" ? "+" : line.kind === "remove" ? "-" : " "}
                    </span>
                    <span className="sr-only">{line.kind === "add" ? "Added: " : line.kind === "remove" ? "Removed: " : ""}</span>
                    <span className={clsx(line.kind === "remove" && "opacity-70")}>
                      {lineTokens ? lineTokens.map((t, k) => <span key={k} style={{ color: t.color }}>{t.content}</span>) : line.text || " "}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </Collapse>
    </div>
  );
}

export function DiffBlock({ files }: { files: DiffFile[] }) {
  return (
    <section className="overflow-hidden rounded-[var(--radius-card)] border border-line bg-[#070707]" aria-label="Code changes">
      {files.map((file) => (
        <DiffFileView key={file.path} file={file} />
      ))}
    </section>
  );
}

export function PrCard({ pr }: { pr: PullRequest }) {
  return (
    <section className="flex flex-wrap items-start gap-4 rounded-[var(--radius-card)] border border-line bg-card px-4 py-4 sm:flex-nowrap">
      <span className="grid size-10 shrink-0 place-items-center rounded-full bg-accent/12 text-accent">
        <GitPullRequest weight="bold" className="size-5" aria-hidden />
      </span>
      <div className="min-w-0 flex-1 basis-48">
        <p className="text-[0.95rem] font-semibold leading-snug text-fg">
          {pr.title ?? "Pull request"} {pr.number !== null && <span className="font-normal text-fg-subtle">#{pr.number}</span>}
        </p>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.8rem]">
          {pr.branch && (
            <span className="inline-flex items-center gap-1.5 font-mono text-fg-muted">
              <GitBranch weight="bold" className="size-3.5" aria-hidden />
              {pr.branch}
              {pr.base && <span className="text-fg-subtle">into {pr.base}</span>}
            </span>
          )}
          <span className="rounded-full border border-line-strong px-2 py-0.5 text-[0.72rem] text-fg-muted">{pr.fromFork ? "from fork" : "direct"}</span>
        </p>
      </div>
      {pr.url && (
        <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-line-strong px-3.5 text-[0.82rem] text-fg transition-colors duration-150 hover:border-accent/50 hover:text-accent">
          GitHub
          <ArrowSquareOut weight="bold" className="size-4" aria-hidden />
        </a>
      )}
    </section>
  );
}

export function ActionRow({ block }: { block: Extract<Block, { kind: "action" }> }) {
  return (
    <p className="flex items-center gap-2.5 px-1 font-mono text-[0.8rem]">
      {block.running ? (
        <CircleNotch weight="bold" className="size-4 shrink-0 animate-spin text-progress motion-reduce:animate-none" aria-hidden />
      ) : block.stopped ? (
        <X weight="bold" className="size-4 shrink-0 text-fg-subtle" aria-hidden />
      ) : block.error ? (
        <X weight="bold" className="size-4 shrink-0 text-fail" aria-hidden />
      ) : (
        <Wrench weight="bold" className="size-4 shrink-0 text-fg-subtle" aria-hidden />
      )}
      <span className="text-fg-muted">{block.server ? `${block.server} ` : ""}</span>
      <span className="text-fg">{block.tool}</span>
      {block.target && <span className="truncate text-fg-subtle">{block.target}</span>}
      {block.error && <span className="truncate text-fail" title={block.error}>{block.error}</span>}
    </p>
  );
}

export { CopyButton };

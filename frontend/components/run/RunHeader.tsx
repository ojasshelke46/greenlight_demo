"use client";

import { ArrowLeft, GitBranch, GitFork, Globe, Handshake, LockSimple } from "@phosphor-icons/react";
import { MODE_LABEL, accessLabel, type RunState } from "@/lib/state";

const CONNECTION_LABEL: Record<RunState["connection"], string> = {
  live: "Live",
  reconnecting: "Reconnecting",
  ended: "Run ended",
};

export function RunHeader({ run, onNewRun }: { run: RunState; onNewRun: () => void }) {
  const AccessIcon = run.viaFork ? GitFork : Handshake;
  return (
    <header className="flex min-h-16 flex-wrap items-center gap-x-8 gap-y-3 border-b border-ink-700 px-4 py-3 md:px-8">
      <button
        type="button"
        onClick={onNewRun}
        className="flex items-center gap-2 rounded-xl py-1 text-lg font-semibold tracking-tight transition-colors hover:text-fg-muted"
        aria-label="New run"
      >
        <ArrowLeft weight="bold" className="size-5" aria-hidden />
        Greenlight
      </button>

      <p className="min-w-0 truncate font-mono text-xl font-medium" title={run.repo}>
        {run.repo}
      </p>

      <dl className="flex flex-wrap items-center gap-x-6 gap-y-2 text-base">
        <div className="flex items-center gap-2">
          <dt className="text-fg-muted">Mode</dt>
          <dd className="font-semibold">{MODE_LABEL[run.mode]}</dd>
        </div>
        <div className="flex items-center gap-2">
          <dt className="sr-only">Access</dt>
          <AccessIcon weight="bold" className="size-5 text-fg-muted" aria-hidden />
          <dd>{accessLabel(run)}</dd>
        </div>
        <div className="flex items-center gap-2">
          <dt className="sr-only">Visibility</dt>
          {run.private ? (
            <LockSimple weight="bold" className="size-5 text-fg-muted" aria-hidden />
          ) : (
            <Globe weight="bold" className="size-5 text-fg-muted" aria-hidden />
          )}
          <dd>{run.private ? "Private" : "Public"}</dd>
        </div>
        <div className="flex items-center gap-2">
          <dt className="sr-only">Default branch</dt>
          <GitBranch weight="bold" className="size-5 text-fg-muted" aria-hidden />
          <dd className="font-mono">{run.defaultBranch}</dd>
        </div>
      </dl>

      <p className="ml-auto text-base text-fg-muted" aria-live="polite">
        {CONNECTION_LABEL[run.connection]}
      </p>
    </header>
  );
}

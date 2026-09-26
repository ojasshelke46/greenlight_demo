"use client";

import { ArrowRight, ArrowSquareOut, CircleNotch, ListChecks, Play, Stop } from "@phosphor-icons/react";
import clsx from "clsx";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { campaignApi, usePolled, type PolicyCampaign, type PolicyItem } from "@/lib/campaigns";

const STATUS: Record<PolicyItem["status"], { label: string; className: string; dot: string }> = {
  queued: { label: "Queued", className: "text-fg-subtle", dot: "border border-line-strong" },
  drafting: { label: "Drafting", className: "text-progress", dot: "dot-pulse bg-progress" },
  pr_opened: { label: "PR open", className: "text-accent", dot: "bg-accent" },
  failed: { label: "Failed", className: "text-fail", dot: "bg-fail" },
  skipped: { label: "Skipped", className: "text-fg-subtle", dot: "bg-line-strong" },
};

const busy = (p: PolicyCampaign) => p.active !== null;
const loadPolicy = (id: string) => campaignApi.getPolicy(id);

/** Policy: one .greenlight.yml draft and PR per repo, one repo at a time. */
export function PolicyCampaignView({ campaignId, onOpenRun }: { campaignId: string; onOpenRun: (runId: string) => void }) {
  const { value: policy, error, refresh } = usePolled(campaignId, loadPolicy, busy);
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const act = async (key: string, action: () => Promise<unknown>) => {
    setPending(key);
    setActionError(null);
    try {
      await action();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "That did not work");
    } finally {
      setPending(null);
      refresh();
    }
  };

  if (!policy) return <p className="mx-auto w-full max-w-3xl px-5 pt-8 text-[0.9rem] text-fg-muted">{error ?? "Loading the policy campaign"}</p>;
  const current = policy.active ?? [...policy.items].reverse().find((i) => i.status !== "queued") ?? policy.items[0];

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-5 pb-10 pt-8">
      <header className="flex items-center gap-3">
        <span className="grid size-10 place-items-center rounded-full border border-line-strong bg-card text-fg-muted">
          <ListChecks weight="bold" className="size-5" aria-hidden />
        </span>
        <div className="min-w-0">
          <h2 className="font-display text-[1.1rem] font-semibold text-fg">Set the rules</h2>
          <p className="text-[0.85rem] text-fg-muted">
            {policy.items.filter((i) => i.status === "pr_opened").length} of {policy.items.length} repos have a policy PR
          </p>
        </div>
      </header>

      {current && (
        <section className="rounded-[var(--radius-card)] border border-line bg-panel/80 p-4" aria-live="polite">
          <p className="flex items-center gap-2">
            <span aria-hidden className={clsx("size-2 rounded-full", STATUS[current.status].dot)} />
            <span className="min-w-0 flex-1 truncate font-mono text-[0.95rem] text-fg">{current.repo}</span>
            <span className={clsx("text-[0.82rem] font-medium", STATUS[current.status].className)}>{STATUS[current.status].label}</span>
          </p>
          {current.reason && <p className="mt-2 text-[0.85rem] text-fg-muted">{current.reason}</p>}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {current.pr_url && (
              <a href={current.pr_url} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line-strong px-3.5 text-[0.85rem] text-fg transition-colors duration-150 hover:border-accent/50 hover:text-accent">
                Open the PR
                <ArrowSquareOut weight="bold" className="size-4" aria-hidden />
              </a>
            )}
            {current.run_id && (
              <button type="button" onClick={() => onOpenRun(current.run_id!)} className="inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[0.85rem] text-fg-muted transition-colors duration-150 hover:text-fg">
                See the draft
                <ArrowRight weight="bold" className="size-4" aria-hidden />
              </button>
            )}
          </div>
        </section>
      )}

      {policy.stopped && <p className="text-[0.85rem] text-fail">Stopped at {policy.stopped.repo}: {policy.stopped.reason}</p>}
      {actionError && <p role="alert" className="text-[0.85rem] text-fail">{actionError}</p>}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={policy.next === null || policy.active !== null || pending !== null}
          onClick={() => act("next", () => campaignApi.policyNext(campaignId))}
          className="inline-flex h-10 items-center gap-1.5 rounded-full bg-accent px-4 text-[0.88rem] font-semibold text-canvas transition-[opacity,transform] duration-150 ease-out active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {pending === "next" ? <CircleNotch weight="bold" className="size-4 animate-spin motion-reduce:animate-none" aria-hidden /> : <Play weight="fill" className="size-4" aria-hidden />}
          Draft next
        </button>
        {policy.auto && policy.next !== null && (
          <button type="button" disabled={pending !== null} onClick={() => act("stop", () => campaignApi.policyStop(campaignId))} className="inline-flex h-10 items-center gap-1.5 rounded-full px-3 text-[0.85rem] text-fg-muted transition-colors duration-150 hover:text-fg">
            <Stop weight="fill" className="size-3.5" aria-hidden />
            Stop after this one
          </button>
        )}
      </div>

      <ol className="flex flex-col gap-1.5" aria-label="Repos">
        {policy.items.map((item) => (
          <li key={item.repo} className="flex items-center gap-3 rounded-full border border-line bg-card/50 px-4 py-2">
            <span aria-hidden className={clsx("size-2 rounded-full", STATUS[item.status].dot)} />
            <span className="min-w-0 flex-1 truncate font-mono text-[0.82rem] text-fg-muted">{item.repo}</span>
            {item.pr_url ? (
              <a href={item.pr_url} target="_blank" rel="noreferrer" className="text-[0.8rem] text-accent underline decoration-accent/40 underline-offset-4">
                PR
              </a>
            ) : (
              <span className={clsx("text-[0.8rem]", STATUS[item.status].className)}>{STATUS[item.status].label}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

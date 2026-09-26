"use client";

import { ArrowRight, CircleNotch, ListChecks, NotePencil, Warning } from "@phosphor-icons/react";
import clsx from "clsx";
import { motion } from "motion/react";
import { useState, type ReactNode } from "react";
import { ApiError, draftPolicy } from "@/lib/api";
import { usePolicy, type PolicyInfo } from "@/lib/features/policy";

// Rules arrive just after the sheet opens: fade in, no movement, since the sheet itself is sliding.
const APPEAR = { duration: 0.2, ease: [0.23, 1, 0.32, 1] } as const;
const MAX_AGE_MS = 15_000;

export function RepoRules({ repo, onOpenRun }: { repo: string | null; onOpenRun?: (runId: string) => void }) {
  const fetched = usePolicy(repo, MAX_AGE_MS);

  return (
    <section aria-labelledby="repo-rules-title">
      <h3 id="repo-rules-title" className="flex items-center gap-2.5 font-display text-[1rem] font-semibold text-fg">
        <ListChecks weight="bold" className="size-5 text-fg-muted" aria-hidden />
        Repo rules
      </h3>

      {!repo ? (
        <p className="mt-3 text-[0.85rem] text-fg-muted">Start or open a run to see the rules its repo sets for merging.</p>
      ) : !fetched || fetched.status === "loading" ? (
        <div className="mt-3 flex flex-col gap-2" aria-label="Loading the repo rules">
          <div className="h-4 w-2/3 animate-pulse rounded-full bg-card motion-reduce:animate-none" />
          <div className="h-4 w-1/2 animate-pulse rounded-full bg-card motion-reduce:animate-none" />
        </div>
      ) : fetched.status === "failed" ? (
        <p className="mt-3 text-[0.85rem] text-fail">{fetched.message}</p>
      ) : (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={APPEAR}>
          <Rules info={fetched.info} />
          {!fetched.info.exists && !fetched.info.error && <DraftPolicy repo={fetched.info.repo} onOpenRun={onOpenRun} />}
        </motion.div>
      )}
    </section>
  );
}

/** The repo has no policy file: the policy agent can draft one in a top level run of its own. */
function DraftPolicy({ repo, onOpenRun }: { repo: string; onOpenRun?: (runId: string) => void }) {
  const [state, setState] = useState<{ kind: "idle" } | { kind: "starting" } | { kind: "started"; runId: string } | { kind: "failed"; message: string }>({ kind: "idle" });

  if (state.kind === "started") {
    return (
      <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius-card)] border border-accent/30 bg-accent/[0.05] px-4 py-3 text-[0.85rem] text-fg">
        The policy agent is drafting a <span className="font-mono text-[0.8rem]">.greenlight.yml</span>.
        {onOpenRun && (
          <button type="button" onClick={() => onOpenRun(state.runId)} className="inline-flex items-center gap-1.5 font-medium text-accent underline decoration-accent/40 underline-offset-4 transition-colors duration-150 hover:decoration-accent">
            Open the policy run
            <ArrowRight weight="bold" className="size-3.5" aria-hidden />
          </button>
        )}
      </p>
    );
  }
  return (
    <div className="mt-3 flex flex-col gap-2">
      <button
        type="button"
        disabled={state.kind === "starting"}
        onClick={async () => {
          setState({ kind: "starting" });
          try {
            setState({ kind: "started", runId: await draftPolicy(`https://github.com/${repo}`) });
          } catch (e) {
            setState({ kind: "failed", message: e instanceof ApiError ? e.message : "Could not start the policy agent" });
          }
        }}
        className="inline-flex h-9 w-fit items-center gap-1.5 rounded-full border border-accent/40 bg-accent/[0.07] px-3.5 text-[0.85rem] font-medium text-accent transition-[background-color,transform] duration-150 ease-out hover:bg-accent/15 active:scale-[0.97] disabled:cursor-wait disabled:opacity-60"
      >
        {state.kind === "starting" ? <CircleNotch weight="bold" className="size-4 animate-spin motion-reduce:animate-none" aria-hidden /> : <NotePencil weight="bold" className="size-4" aria-hidden />}
        {state.kind === "starting" ? "Starting the policy agent" : "Draft a policy"}
      </button>
      {state.kind === "failed" && (
        <p role="alert" className="text-[0.85rem] text-fail">
          {state.message}
        </p>
      )}
    </div>
  );
}

function Rules({ info }: { info: PolicyInfo }) {
  const source = (
    <>
      <span className="font-mono text-[0.8rem] text-fg">{info.path}</span> in <span className="font-mono text-[0.8rem] text-fg">{info.repo}</span>
    </>
  );

  if (info.error || !info.policy) {
    return (
      <>
        <p className="mt-1 text-[0.82rem] text-fg-muted">From {source}</p>
        <p role="alert" className="mt-3 flex items-start gap-2 rounded-[var(--radius-card)] border border-fail/30 bg-fail/10 px-4 py-3 text-[0.85rem] leading-relaxed text-fail">
          <Warning weight="bold" className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            This policy file is invalid, so every merge is refused until it is fixed. <span className="break-words font-mono text-[0.8rem]">{info.error}</span>
          </span>
        </p>
      </>
    );
  }

  const { policy, freeze } = info;
  return (
    <>
      <p className="mt-1 text-[0.82rem] text-fg-muted">
        {info.exists ? <>From {source}</> : <>No <span className="font-mono text-[0.8rem] text-fg">{info.path}</span> in <span className="font-mono text-[0.8rem] text-fg">{info.repo}</span>, so the defaults apply.</>}
      </p>
      <dl className="mt-3 flex flex-col gap-2">
        <Rule label="Approvers">
          {policy.approvers.length === 0 ? (
            "Anyone"
          ) : (
            <span className="flex flex-wrap gap-1.5">
              {policy.approvers.map((login) => (
                <span key={login} className="rounded-full border border-line px-2 py-0.5 font-mono text-[0.78rem] text-fg">
                  {login}
                </span>
              ))}
            </span>
          )}
        </Rule>
        <Rule label="Approvals required">
          {policy.required_approvals} {policy.required_approvals === 1 ? "approval" : "different approvers"}
        </Rule>
        <Rule label="Major upgrades">{policy.allow_major_upgrades ? "Allowed" : "Not allowed. The agent stays within the current major version."}</Rule>
        <Rule label="Merge freeze">
          {freeze.active ? (
            <span className="text-fail">Frozen until {freeze.until}</span>
          ) : policy.freeze.windows.length === 0 ? (
            "No freeze windows"
          ) : (
            <span className="flex flex-col gap-0.5">
              <span>Not frozen right now</span>
              {policy.freeze.windows.map((w) => (
                <span key={`${w.start}${w.end}`} className="font-mono text-[0.78rem] text-fg-muted">
                  {w.start} to {w.end} {policy.freeze.timezone}
                </span>
              ))}
            </span>
          )}
        </Rule>
      </dl>
    </>
  );
}

function Rule({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={clsx("rounded-[var(--radius-card)] border border-line bg-card px-4 py-3", className)}>
      <dt className="text-[0.78rem] font-medium text-fg-subtle">{label}</dt>
      <dd className="mt-0.5 text-[0.85rem] leading-relaxed text-fg">{children}</dd>
    </div>
  );
}

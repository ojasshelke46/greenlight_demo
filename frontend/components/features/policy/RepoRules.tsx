"use client";

import { ListChecks, Warning } from "@phosphor-icons/react";
import clsx from "clsx";
import { motion } from "motion/react";
import type { ReactNode } from "react";
import { usePolicy, type PolicyInfo } from "@/lib/features/policy";

// Rules arrive just after the sheet opens: fade in, no movement, since the sheet itself is sliding.
const APPEAR = { duration: 0.2, ease: [0.23, 1, 0.32, 1] } as const;
const MAX_AGE_MS = 15_000;

export function RepoRules({ repo }: { repo: string | null }) {
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
        </motion.div>
      )}
    </section>
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

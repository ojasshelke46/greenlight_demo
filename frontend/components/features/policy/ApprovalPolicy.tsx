"use client";

import { Check, HourglassMedium, Warning } from "@phosphor-icons/react";
import clsx from "clsx";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, type ReactNode } from "react";
import { useApproval } from "@/lib/features/approval";
import { recordApprovalResult, sameLogin, usePolicy, usePolicyState, waitingMessage } from "@/lib/features/policy";
import type { RunState } from "@/lib/state";

// The waiting line is state indication after a hold: occasional, 220ms strong ease out, a short lift.
const ENTER = { duration: 0.22, ease: [0.23, 1, 0.32, 1] } as const;
// Freeze windows open and close by the clock, so the card always checks fresh rules when it opens.
const MAX_AGE_MS = 0;
const NO_APPROVERS: string[] = [];

/**
 * Above the hold control: why a merge would be refused before anyone holds, who is approving, and after a
 * 202 how many approvals are still missing. A 409 is not repeated here; the card already shows it in red.
 */
export function ApprovalPolicy({ run }: { run: RunState }) {
  const reduce = useReducedMotion();
  const fetched = usePolicy(run.repo, MAX_AGE_MS);
  const { approver, setApprover, lastResult } = useApproval();
  const { lastResult: recorded } = usePolicyState();

  useEffect(() => {
    if (lastResult) recordApprovalResult(lastResult);
  }, [lastResult]);

  const info = fetched?.status === "ready" ? fetched.info : null;
  const approvers = info?.policy?.approvers ?? NO_APPROVERS;
  const waiting = recorded?.kind === "need_more" && recorded.runId === run.id ? recorded : null;
  const approvedBy = waiting?.approver ?? null;
  // Once the server has refused for this freeze, the card shows that refusal in red; do not say it twice.
  const refusedForFreeze =
    recorded?.kind === "denied" && recorded.runId === run.id && info?.freeze.until != null && recorded.reason === `Merges frozen until ${info.freeze.until}`;

  // The hold always belongs to someone the policy allows and who has not approved yet: after a 202 the
  // selection moves on to the next person, so the next hold is theirs.
  useEffect(() => {
    if (approvers.length === 0) return;
    const allowed = approvers.some((login) => sameLogin(login, approver));
    const alreadyApproved = approvedBy !== null && sameLogin(approvedBy, approver);
    if (allowed && !alreadyApproved) return;
    const next = approvers.find((login) => approvedBy === null || !sameLogin(login, approvedBy));
    if (next && !sameLogin(next, approver)) setApprover(next);
  }, [approvers, approver, approvedBy, setApprover]);

  return (
    <div className="mt-5 flex flex-col gap-3">
      {info?.error && <Blocked>Merging is blocked: the repo policy is invalid. {info.error}</Blocked>}
      {info?.freeze.active && !refusedForFreeze && <Blocked>Merges frozen until {info.freeze.until}. An approval now will be refused.</Blocked>}
      {fetched?.status === "failed" && <p className="text-[0.82rem] text-fg-muted">{fetched.message}. The server still checks the rules on approval.</p>}

      {!info?.error && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[0.82rem] font-medium text-fg-subtle">Approving as</span>
          {approvers.length > 0 ? (
            <div role="radiogroup" aria-label="Approver" className="flex flex-wrap gap-1.5">
              {approvers.map((login) => {
                const selected = sameLogin(login, approver);
                const done = approvedBy !== null && sameLogin(login, approvedBy);
                return (
                  <button
                    key={login}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={done}
                    onClick={() => setApprover(login)}
                    className={clsx(
                      "flex h-8 items-center gap-1.5 rounded-full border px-3 font-mono text-[0.8rem] transition-[color,background-color,border-color,transform] duration-150 ease-out active:scale-[0.97]",
                      selected ? "border-accent/50 bg-accent/10 text-accent" : "border-line bg-card text-fg-muted hover:text-fg",
                      done && "cursor-not-allowed border-line text-fg-subtle active:scale-100",
                    )}
                  >
                    {done && <Check weight="bold" className="size-3.5" aria-hidden />}
                    {login}
                    {done && <span className="sr-only">, already approved</span>}
                  </button>
                );
              })}
            </div>
          ) : (
            <input
              value={approver}
              onChange={(event) => setApprover(event.target.value)}
              aria-label="Approver"
              placeholder="Your GitHub login"
              className="h-8 w-48 rounded-full border border-line bg-card px-3 font-mono text-[0.8rem] text-fg outline-none transition-colors duration-150 placeholder:text-fg-subtle focus-visible:border-accent/50"
            />
          )}
        </div>
      )}

      {waiting && (
        <motion.p
          key={waiting.decidedAt}
          role="status"
          initial={reduce ? { opacity: 0 } : { opacity: 0, transform: "translateY(6px)" }}
          animate={{ opacity: 1, transform: "translateY(0px)" }}
          transition={ENTER}
          className="flex items-start gap-2 rounded-xl border border-progress/30 bg-progress/10 px-4 py-3 text-[0.9rem] text-progress"
        >
          <HourglassMedium weight="bold" className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            {waitingMessage(waiting.reason)}. <span className="text-fg-muted">Approved so far by <span className="font-mono text-[0.85em]">{waiting.approver}</span>.</span>
          </span>
        </motion.p>
      )}
    </div>
  );
}

function Blocked({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-start gap-2 rounded-xl border border-fail/30 bg-fail/10 px-4 py-3 text-[0.9rem] text-fail">
      <Warning weight="bold" className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}

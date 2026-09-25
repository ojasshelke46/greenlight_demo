"use client";

import { Dialog } from "@base-ui/react/dialog";
import { CheckCircle, CircleNotch, Hand, Prohibit, RocketLaunch, X, XCircle } from "@phosphor-icons/react";
import clsx from "clsx";
import { useEffect, useState, type ReactNode } from "react";
import { fetchLedger, type Ledger } from "@/lib/api";
import { POLICY } from "@/lib/policy";

// Side sheets: occasional, 360ms on the iOS drawer curve, entering and leaving from the right.
function Sheet({ open, onOpenChange, title, description, width, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; width: string; children: ReactNode }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/55 transition-opacity duration-200 ease-out data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Dialog.Popup
          className={clsx(
            "fixed inset-y-0 right-0 z-50 flex max-w-full flex-col border-l border-line bg-panel shadow-[-30px_0_80px_-20px_rgb(0_0_0/0.8)] outline-none transition-transform duration-[360ms] ease-[var(--ease-drawer)] data-ending-style:translate-x-full data-starting-style:translate-x-full motion-reduce:transition-opacity motion-reduce:data-ending-style:translate-x-0 motion-reduce:data-ending-style:opacity-0 motion-reduce:data-starting-style:translate-x-0 motion-reduce:data-starting-style:opacity-0",
            width,
          )}
        >
          <header className="flex items-start gap-4 border-b border-line px-6 py-5">
            <div className="min-w-0 flex-1">
              <Dialog.Title className="font-display text-[1.25rem] font-semibold text-fg">{title}</Dialog.Title>
              <Dialog.Description className="mt-1 text-[0.85rem] text-fg-muted">{description}</Dialog.Description>
            </div>
            <Dialog.Close aria-label="Close" className="grid size-9 place-items-center rounded-full text-fg-muted transition-colors duration-150 hover:bg-card hover:text-fg">
              <X weight="bold" className="size-[18px]" aria-hidden />
            </Dialog.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const POLICY_ICON = {
  auto: { Icon: RocketLaunch, className: "text-fg-muted" },
  approval: { Icon: Hand, className: "text-accent" },
  never: { Icon: Prohibit, className: "text-fail" },
} as const;

export function PolicySheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Policy" description="What the agent may do in every run." width="w-[440px]">
      <div className="flex flex-col gap-7">
        {POLICY.map((section) => {
          const { Icon, className } = POLICY_ICON[section.id];
          return (
            <section key={section.id}>
              <h3 className="flex items-center gap-2.5 font-display text-[1rem] font-semibold text-fg">
                <Icon weight="bold" className={clsx("size-5", className)} aria-hidden />
                {section.title}
              </h3>
              <ul className="mt-3 flex flex-col gap-2">
                {section.tools.map((tool) => (
                  <li key={tool.name} className={clsx("rounded-[var(--radius-card)] border px-4 py-3", section.id === "approval" ? "border-accent/30 bg-accent/[0.05]" : "border-line bg-card")}>
                    <p className="font-mono text-[0.85rem] text-fg">{tool.name}</p>
                    <p className="mt-0.5 text-[0.82rem] text-fg-muted">{tool.note}</p>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
        <p className="rounded-[var(--radius-card)] border border-line bg-card px-4 py-3 text-[0.85rem] leading-relaxed text-fg">
          The backend revalidates every approval before anything merges.
        </p>
      </div>
    </Sheet>
  );
}

function time(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function eventSummary(type: string, payload: Record<string, unknown>): string {
  if (type === "tool.response") return `tool ${String(payload.tool_call_id ?? "").slice(-8)}`;
  if (type === "turn.done") return `status ${String((payload.state as { status?: string } | undefined)?.status ?? "")}`;
  if (type === "tool.approval_required") return "waiting for a decision";
  if (type === "model.message") {
    const calls = Array.isArray(payload.tool_calls) ? payload.tool_calls.length : 0;
    return calls > 0 ? `${calls} tool ${calls === 1 ? "call" : "calls"}` : "message";
  }
  return "";
}

export function LedgerDrawer({ runId, open, onOpenChange }: { runId: string | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Audit ledger" description="Append only record of every event and every approval attempt for this run." width="w-[560px]">
      {runId ? <LedgerTimeline key={runId} runId={runId} /> : <p className="text-[0.9rem] text-fg-muted">Start or open a run to see its ledger.</p>}
    </Sheet>
  );
}

function LedgerTimeline({ runId }: { runId: string }) {
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchLedger(runId)
      .then((l) => !cancelled && setLedger(l))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const events = ledger?.events.filter((e) => e.type !== "model.message.delta") ?? [];
  const deltas = (ledger?.events.length ?? 0) - events.length;

  if (error) return <p className="text-[0.9rem] text-fail">{error}</p>;
  if (!ledger) {
    return (
      <p className="flex items-center gap-2 text-[0.9rem] text-fg-muted">
        <CircleNotch weight="bold" className="size-4 animate-spin" aria-hidden />
        Loading the ledger
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-7">
      <section>
        <h3 className="font-display text-[1rem] font-semibold text-fg">Approvals</h3>
        {ledger.approvals.length === 0 ? (
          <p className="mt-2 text-[0.85rem] text-fg-muted">No approval attempts.</p>
        ) : (
          <ol className="mt-3 flex flex-col gap-2">
            {ledger.approvals.map((a, i) => {
              const accepted = a.result === "accepted";
              return (
                <li key={i} className="flex items-start gap-3 rounded-[var(--radius-card)] border border-line bg-card px-4 py-3">
                  {accepted ? <CheckCircle weight="fill" className="mt-0.5 size-5 shrink-0 text-accent" aria-hidden /> : <XCircle weight="fill" className="mt-0.5 size-5 shrink-0 text-fail" aria-hidden />}
                  <div className="min-w-0 flex-1 text-[0.85rem]">
                    <p className="text-fg">
                      <span className="font-semibold">{a.approver}</span> chose {a.decision} on <span className="font-mono">{a.tool_name}</span>
                    </p>
                    <p className={clsx("mt-0.5", accepted ? "text-fg-muted" : "text-fail")}>{a.result}</p>
                  </div>
                  <span className="shrink-0 font-mono text-[0.75rem] text-fg-subtle">{time(a.decided_at)}</span>
                </li>
              );
            })}
          </ol>
        )}
      </section>
      <section>
        <h3 className="font-display text-[1rem] font-semibold text-fg">Events</h3>
        <p className="mt-1 text-[0.8rem] text-fg-subtle">
          {events.length} events, plus {deltas} streaming text deltas
        </p>
        <ol className="relative mt-4 flex flex-col border-l border-line pl-5">
          {events.map((e) => (
            <li key={e.sequence} className="relative py-1.5">
              <span aria-hidden className={clsx("absolute -left-[23.5px] top-3 size-2 rounded-full", e.type === "turn.done" || e.type === "tool.approval_required" ? "bg-accent" : "bg-line-strong")} />
              <p className="flex items-baseline gap-3 font-mono text-[0.78rem]">
                <span className="w-12 shrink-0 text-fg-subtle">#{e.sequence}</span>
                <span className="text-fg">{e.type}</span>
                <span className="truncate text-fg-muted">{eventSummary(e.type, e.payload)}</span>
                <span className="ml-auto shrink-0 text-fg-subtle">{time(e.received_at)}</span>
              </p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

"use client";

import { Check, CircleNotch, DownloadSimple, Notebook, Warning } from "@phosphor-icons/react";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";
import { ApiError, requestAuditReport } from "@/lib/api";

export type VerifyResult = {
  ok: boolean;
  entries: number;
  signed: boolean;
  first_broken_idx: number | null;
  reason: string | null;
};

type Phase = { kind: "checking" } | { kind: "ready"; result: VerifyResult } | { kind: "unavailable" };

const PRE_FLIGHT_RECORDER = "created before flight recorder";
const SIGNATURE_NOT_CHECKED = "signature not checked: LEDGER_HMAC_KEY is not set";

function isVerifyResult(value: unknown): value is VerifyResult {
  const v = value as Partial<VerifyResult> | null;
  return typeof v?.ok === "boolean" && typeof v.entries === "number" && typeof v.signed === "boolean";
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function Separator() {
  return (
    <span aria-hidden className="text-fg-subtle">
      {" · "}
    </span>
  );
}

function VerifyLine({ result }: { result: VerifyResult }) {
  if (result.ok) {
    const signature = !result.signed ? "unsigned" : result.reason === SIGNATURE_NOT_CHECKED ? "signature not checked" : "signed";
    return (
      <p className="flex items-start gap-2 font-mono text-[1.05rem] leading-relaxed text-fg">
        <Check weight="bold" className="mt-[0.3em] size-4 shrink-0" aria-hidden />
        <span>
          Flight recorder
          <Separator />
          chain verified
          <Separator />
          {plural(result.entries, "entry", "entries")}
          <Separator />
          <span className={signature === "signed" ? undefined : "text-fg-muted"}>{signature}</span>
        </span>
      </p>
    );
  }

  // Not tampering: the run simply has no chain to check.
  if (result.reason === PRE_FLIGHT_RECORDER) {
    return (
      <p className="font-mono text-[1.05rem] leading-relaxed text-fg-muted">
        Flight recorder
        <Separator />
        this run was created before it existed
      </p>
    );
  }

  // Broken means blocked, so it is the one state shown in red.
  const where = result.first_broken_idx !== null ? `broken at entry ${result.first_broken_idx}` : "broken";
  return (
    <p className="flex items-start gap-2 font-mono text-[1.05rem] leading-relaxed text-fail">
      <Warning weight="bold" className="mt-[0.3em] size-4 shrink-0" aria-hidden />
      <span>
        Flight recorder
        <Separator />
        chain {where}
        <Separator />
        {result.reason ?? "verification failed"}
      </span>
    </p>
  );
}

function ExportLink({ runId, format, label }: { runId: string; format: "md" | "json"; label: string }) {
  return (
    <a
      href={`/api/audit/export?runId=${encodeURIComponent(runId)}&format=${format}`}
      download={`greenlight_${runId}.${format}`}
      className="inline-flex h-9 items-center gap-1.5 rounded-full border border-line-strong px-3.5 text-[0.88rem] text-fg-muted transition-colors duration-150 hover:border-fg-subtle hover:text-fg"
    >
      <DownloadSimple weight="bold" className="size-4" aria-hidden />
      {label}
    </a>
  );
}

/** Starts the auditor on this run's export; its report streams into the ledger drawer. */
function ReportButton({ runId, onReport }: { runId: string; onReport: (auditorRunId: string) => void }) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        disabled={starting}
        onClick={async () => {
          setStarting(true);
          setError(null);
          try {
            onReport(await requestAuditReport(runId));
          } catch (e) {
            setError(e instanceof ApiError ? e.message : "Could not start the auditor");
          } finally {
            setStarting(false);
          }
        }}
        className="inline-flex h-9 items-center gap-1.5 rounded-full border border-accent/40 bg-accent/[0.07] px-3.5 text-[0.88rem] font-medium text-accent transition-[background-color,transform] duration-150 ease-out hover:bg-accent/15 active:scale-[0.97] disabled:cursor-wait disabled:opacity-60"
      >
        {starting ? <CircleNotch weight="bold" className="size-4 animate-spin motion-reduce:animate-none" aria-hidden /> : <Notebook weight="bold" className="size-4" aria-hidden />}
        {starting ? "Starting the auditor" : "Generate audit report"}
      </button>
      {error && (
        <p role="alert" className="basis-full text-[0.85rem] text-fail">
          {error}
        </p>
      )}
    </>
  );
}

export function AuditPanel({ runId, onReport }: { runId: string; onReport?: (auditorRunId: string) => void }) {
  const reduce = useReducedMotion();
  const [phase, setPhase] = useState<Phase>({ kind: "checking" });

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/audit/verify?runId=${encodeURIComponent(runId)}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const body: unknown = response.ok ? await response.json() : null;
        if (!controller.signal.aborted) setPhase(isVerifyResult(body) ? { kind: "ready", result: body } : { kind: "unavailable" });
      })
      .catch(() => {
        if (!controller.signal.aborted) setPhase({ kind: "unavailable" });
      });
    return () => controller.abort();
  }, [runId]);

  if (phase.kind === "checking") {
    return (
      <p role="status" className="px-5 text-[0.95rem] text-fg-subtle">
        Checking the flight recorder
      </p>
    );
  }

  if (phase.kind === "unavailable") {
    return (
      <p role="status" className="px-5 text-[0.95rem] text-fg-subtle">
        Flight recorder unavailable
      </p>
    );
  }

  return (
    // Entrance: occasional, state indication. Strong ease out, 220ms, a 4px lift; opacity only under reduced motion.
    <motion.div
      role="status"
      className="flex flex-col gap-3 px-5"
      initial={reduce ? { opacity: 0 } : { opacity: 0, transform: "translateY(4px)" }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, transform: "translateY(0px)" }}
      transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
    >
      <VerifyLine result={phase.result} />
      <div className="flex flex-wrap gap-2">
        {onReport && <ReportButton runId={runId} onReport={onReport} />}
        <ExportLink runId={runId} format="md" label="Export report" />
        <ExportLink runId={runId} format="json" label="Export raw" />
      </div>
    </motion.div>
  );
}

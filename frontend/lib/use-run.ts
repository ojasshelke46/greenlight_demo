"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, controlRun, fetchAcceptedDecision, fetchRelease } from "./api";
import type { ApprovalResult } from "./features/approval";
import { RunModel } from "./run-model";
import type { ApprovalState, Release, RunMeta, RunState } from "./state";

const RELEASE_POLL_MS = 3000;

type SubmitApproval = (runId: string, decision: "approve" | "reject") => Promise<ApprovalResult>;

export function useRun() {
  const model = useRef<RunModel | null>(null);
  const source = useRef<EventSource | null>(null);
  const frame = useRef<number | null>(null);
  const approvalRef = useRef<ApprovalState>({ status: "none" });
  const releaseRef = useRef<Release | null>(null);
  const connectionRef = useRef<RunState["connection"]>("connecting");
  const pausingRef = useRef(false);
  const [run, setRun] = useState<RunState | null>(null);

  const render = useCallback(() => {
    frame.current = null;
    if (model.current) setRun(model.current.snapshot(approvalRef.current, releaseRef.current, connectionRef.current, pausingRef.current));
  }, []);

  // Deltas arrive by the thousand; fold every event into the model and render once per frame.
  const schedule = useCallback(() => {
    if (frame.current === null) frame.current = requestAnimationFrame(render);
  }, [render]);

  const setConnection = useCallback(
    (connection: RunState["connection"]) => {
      connectionRef.current = connection;
      schedule();
    },
    [schedule],
  );

  const close = useCallback(() => {
    source.current?.close();
    source.current = null;
  }, []);

  const connect = useCallback(
    (runId: string, after: number) => {
      close();
      setConnection(after > 0 ? "reconnecting" : "connecting");
      const es = new EventSource(`/api/stream?runId=${encodeURIComponent(runId)}${after > 0 ? `&after=${after}` : ""}`);
      source.current = es;

      es.onopen = () => setConnection("live");
      es.onmessage = (message) => {
        const m = model.current;
        if (!m) return;
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(message.data);
        } catch {
          return;
        }
        const sequence = Number(message.lastEventId);
        if (!Number.isFinite(sequence) || sequence <= m.lastSequence) return;
        m.apply(sequence, data);
        if (data.type === "turn.done") connectionRef.current = m.paused ? "paused" : "ended";
        schedule();
      };
      // The backend closes the stream when it has nothing more to send: after a paused turn, or
      // after replaying a finished run. A paused run reopens after the last sequence on resume.
      es.onerror = () => {
        if (source.current !== es) return;
        const m = model.current;
        if (m?.turnFinished) {
          close();
          setConnection(m.paused ? "paused" : "ended");
        } else {
          // EventSource retries on its own and sends Last-Event-ID, which the proxy forwards.
          setConnection("reconnecting");
        }
      };
    },
    [close, schedule, setConnection],
  );

  const open = useCallback(
    (meta: RunMeta) => {
      close();
      model.current = new RunModel(meta);
      approvalRef.current = { status: "none" };
      releaseRef.current = null;
      pausingRef.current = false;
      connectionRef.current = "connecting";
      render();
      connect(meta.id, 0);
      // After a reload the decision lives in the ledger, not in the event stream.
      fetchAcceptedDecision(meta.id)
        .then((row) => {
          if (row?.decision === "approve" || row?.decision === "reject") {
            approvalRef.current = { status: "decided", decision: row.decision, approver: row.approver ?? "someone", decidedAt: row.decided_at ?? "" };
            schedule();
          }
        })
        .catch(() => {});
    },
    [close, connect, render, schedule],
  );

  const decide = useCallback(
    async (decision: "approve" | "reject", submit: SubmitApproval) => {
      const m = model.current;
      const pending = m?.pendingApproval();
      if (!m || !pending) return;
      approvalRef.current = { status: "sending", pending, decision };
      schedule();
      const result = await submit(m.meta.id, decision);
      if (result.kind === "decided") {
        approvalRef.current = { status: "decided", decision: result.decision, approver: result.approver, decidedAt: result.decidedAt };
        connect(m.meta.id, m.lastSequence);
      } else if (result.kind === "need_more") {
        // Recorded, but the agent stays paused until more is given; the reason is in the approval context.
        approvalRef.current = { status: "none" };
      } else {
        approvalRef.current = { status: "refused", pending, reason: result.reason };
      }
      schedule();
    },
    [connect, schedule],
  );

  /** Stop the agent now. The stream then delivers the cancelled turn.done. */
  const pause = useCallback(async (): Promise<string | null> => {
    const m = model.current;
    if (!m) return null;
    pausingRef.current = true;
    schedule();
    try {
      await controlRun(m.meta.id, "pause");
      return null;
    } catch (error) {
      pausingRef.current = false;
      schedule();
      return error instanceof ApiError ? error.message : "Could not pause the run";
    }
  }, [schedule]);

  /** Continue a paused or stopped run: a new turn in the same session, streamed after the last sequence. */
  const resume = useCallback(async (): Promise<string | null> => {
    const m = model.current;
    if (!m) return null;
    try {
      await controlRun(m.meta.id, "resume");
      pausingRef.current = false;
      connect(m.meta.id, m.lastSequence);
      return null;
    } catch (error) {
      return error instanceof ApiError ? error.message : "Could not resume the run";
    }
  }, [connect]);

  const reset = useCallback(() => {
    close();
    model.current = null;
    setRun(null);
  }, [close]);

  // Poll the release once a merge was approved, until GitHub Actions reports a conclusion.
  const approvedMerge = run?.approval.status === "decided" && run.approval.decision === "approve" && run.mode === "ship";
  const concluded = Boolean(run?.release?.conclusion);
  const runId = run?.id;
  useEffect(() => {
    if (!approvedMerge || concluded || !runId) return;
    let stopped = false;
    const tick = async () => {
      try {
        releaseRef.current = await fetchRelease(runId);
        schedule();
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) return;
      }
    };
    tick();
    const timer = setInterval(() => !stopped && tick(), RELEASE_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [approvedMerge, concluded, runId, schedule]);

  useEffect(
    () => () => {
      close();
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    },
    [close],
  );

  return { run, open, decide, pause, resume, reset };
}

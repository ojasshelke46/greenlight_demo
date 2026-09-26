"use client";

// A read only view of any run's event stream, for helper agents shown inside another run: a child's
// handoff block, the auditor's report in the ledger drawer. Each caller gets its own RunModel and its own
// EventSource, so a helper never touches the main run's reducer.

import { useEffect, useRef, useState } from "react";
import { RunModel } from "./run-model";
import type { Role, RunState } from "./state";

const NO_APPROVAL = { status: "none" } as const;

export function useRunStream(runId: string | null, role: Role, repo: string, enabled: boolean): RunState | null {
  const [run, setRun] = useState<RunState | null>(null);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled || !runId) return;
    // Helper runs never merge, so their mode is always pr_only.
    const model = new RunModel({ id: runId, role, repo, mode: "pr_only", viaFork: false, private: null, defaultBranch: null });
    let connection: RunState["connection"] = "connecting";
    const render = () => {
      frame.current = null;
      setRun(model.snapshot(NO_APPROVAL, null, connection));
    };
    const schedule = () => {
      if (frame.current === null) frame.current = requestAnimationFrame(render);
    };

    const es = new EventSource(`/api/stream?runId=${encodeURIComponent(runId)}`);
    es.onopen = () => {
      connection = "live";
      schedule();
    };
    es.onmessage = (message) => {
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(message.data);
      } catch {
        return;
      }
      const sequence = Number(message.lastEventId);
      if (!Number.isFinite(sequence) || sequence <= model.lastSequence) return;
      model.apply(sequence, data);
      if (data.type === "turn.done") connection = "ended";
      schedule();
    };
    // The backend closes the stream once the helper's turn is done; EventSource retries anything else.
    es.onerror = () => {
      if (model.turnFinished) {
        es.close();
        connection = "ended";
      } else {
        connection = "reconnecting";
      }
      schedule();
    };
    schedule();

    return () => {
      es.close();
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [enabled, runId, role, repo]);

  return enabled && run?.id === runId ? run : null;
}

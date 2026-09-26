"use client";

// The helper agent runs a run started, polled every 3 seconds while it is live and once more after it ends,
// so a helper that starts right at the end (the receipt agent) still shows up.

import { useEffect, useState } from "react";
import { fetchChildren } from "./api";
import type { ChildRun } from "./state";

const POLL_MS = 3000;
// The receipt agent starts a few seconds after its run ends, once the receipt settles.
const AFTER_END_MS = 30_000;
// Helpers keep working after the parent ends (the provers run in turn, the receipt settles later).
const HELPER_ACTIVE = new Set(["running", "awaiting_approval", "awaiting_input"]);

export function useChildren(runId: string | null, live: boolean): ChildRun[] {
  const [state, setState] = useState<{ runId: string | null; children: ChildRun[] }>({ runId: null, children: [] });

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const graceUntil = Date.now() + AFTER_END_MS;

    const tick = async () => {
      let children: ChildRun[] | null = null;
      try {
        children = await fetchChildren(runId, controller.signal);
        if (!controller.signal.aborted) setState({ runId, children });
      } catch {
        // A missed poll is retried on the next tick; the blocks keep what they last showed.
      }
      if (controller.signal.aborted) return;
      const helpersActive = children?.some((c) => HELPER_ACTIVE.has(c.status)) ?? false;
      if (live || helpersActive || Date.now() < graceUntil) timer = setTimeout(tick, POLL_MS);
    };

    void tick();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId, live]);

  return state.runId === runId ? state.children : [];
}

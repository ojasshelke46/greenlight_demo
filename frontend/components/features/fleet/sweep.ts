"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

export const SEVERITIES = ["critical", "high", "moderate", "low", "unknown"] as const;
export type Severity = (typeof SEVERITIES)[number];
export type Counts = Record<Severity, number>;

export type Advisory = { id: string; package: string; version: string; severity: Severity; summary: string };

export type RepoResult = {
  repo: string;
  default_branch: string | null;
  status: "ok" | "no_lockfile" | "error";
  error: string | null;
  packages: number;
  counts: Counts;
  top: Advisory[];
  risk: number;
};

export type Summary = {
  target: string;
  totals: { repos: number; ok: number; no_lockfile: number; error: number; counts: Counts; risk: number };
  repos: RepoResult[];
};

export type Phase = "idle" | "sweeping" | "done" | "stopped" | "failed";

export type SweepState = {
  phase: Phase;
  target: string | null;
  rows: RepoResult[];
  summary: Summary | null;
  error: string | null;
};

type Action =
  | { type: "start"; target: string }
  | { type: "repo"; result: RepoResult }
  | { type: "summary"; summary: Summary }
  | { type: "stop" }
  | { type: "fail"; message: string };

export const exposed = (row: RepoResult): boolean => SEVERITIES.some((s) => row.counts[s] > 0);

/** Riskiest first; ties by name so the order is stable while rows stream in. */
function byRisk(a: RepoResult, b: RepoResult): number {
  return b.risk - a.risk || Number(exposed(b)) - Number(exposed(a)) || a.repo.localeCompare(b.repo);
}

const initial: SweepState = { phase: "idle", target: null, rows: [], summary: null, error: null };

function reducer(state: SweepState, action: Action): SweepState {
  switch (action.type) {
    case "start":
      return { phase: "sweeping", target: action.target, rows: [], summary: null, error: null };
    case "repo":
      return { ...state, rows: [...state.rows.filter((r) => r.repo !== action.result.repo), action.result].sort(byRisk) };
    case "summary":
      return { ...state, phase: "done", summary: action.summary, rows: [...action.summary.repos].sort(byRisk) };
    case "stop":
      return state.phase === "sweeping" ? { ...state, phase: "stopped" } : state;
    case "fail":
      return { ...state, phase: "failed", error: action.message };
  }
}

type SseEvent = { event: string; data: string };

/** Server sent events from a fetch body. sse-starlette ends lines with CRLF; a CR split from its LF across chunks is held back. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let pendingCr = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    let text = pendingCr + decoder.decode(value, { stream: true });
    pendingCr = text.endsWith("\r") ? "\r" : "";
    if (pendingCr) text = text.slice(0, -1);
    buffer += text.replace(/\r\n?/g, "\n");

    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = value;
        else if (field === "data") data.push(value);
      }
      if (data.length > 0) yield { event, data: data.join("\n") };
    }
  }
}

export function useSweep() {
  const [state, dispatch] = useReducer(reducer, initial);
  const controller = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    dispatch({ type: "stop" });
  }, []);

  const sweep = useCallback(async (target: string) => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    dispatch({ type: "start", target });

    try {
      const response = await fetch(`/api/features/fleet/scan?target=${encodeURIComponent(target)}`, {
        headers: { Accept: "text/event-stream" },
        cache: "no-store",
        signal: current.signal,
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => null);
        dispatch({ type: "fail", message: typeof body?.detail === "string" ? body.detail : `The sweep could not start (${response.status})` });
        return;
      }
      let finished = false;
      for await (const { event, data } of readEvents(response.body)) {
        if (event === "repo") dispatch({ type: "repo", result: JSON.parse(data) as RepoResult });
        else if (event === "summary") {
          dispatch({ type: "summary", summary: JSON.parse(data) as Summary });
          finished = true;
        }
      }
      if (!finished && !current.signal.aborted) dispatch({ type: "fail", message: "The sweep stopped before it finished. Try again." });
    } catch {
      if (!current.signal.aborted) dispatch({ type: "fail", message: "Could not reach Greenlight to run the sweep." });
    } finally {
      if (controller.current === current) controller.current = null;
    }
  }, []);

  useEffect(() => () => controller.current?.abort(), []);

  return { state, sweep, stop };
}

"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, startRun } from "@/lib/api";
import type { Mode } from "@/lib/state";

type Row = { seq: number; type: string; data: string };

const MAX_ROWS = 3000;

/** Debug view: every event from the backend stream, as it arrives, unprocessed. */
export function RawEvents() {
  const [repo, setRepo] = useState("");
  const [mode, setMode] = useState<Mode>("pr_only");
  const [attachId, setAttachId] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [hideDeltas, setHideDeltas] = useState(false);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState<string | null>(null);
  const source = useRef<EventSource | null>(null);
  const buffer = useRef<Row[]>([]);
  const frame = useRef<number | null>(null);

  const flush = () => {
    frame.current = null;
    const batch = buffer.current;
    buffer.current = [];
    setTotal((t) => t + batch.length);
    setRows((r) => [...r, ...batch].slice(-MAX_ROWS));
  };

  const attach = (id: string) => {
    source.current?.close();
    setRunId(id);
    setRows([]);
    setTotal(0);
    setStatus("connecting");
    const es = new EventSource(`/api/stream?runId=${encodeURIComponent(id)}`);
    source.current = es;
    es.onopen = () => setStatus("live");
    es.onerror = () => setStatus("reconnecting or closed");
    es.onmessage = (m) => {
      let type = "?";
      try {
        type = JSON.parse(m.data).type ?? "?";
      } catch {}
      buffer.current.push({ seq: Number(m.lastEventId), type, data: m.data });
      if (frame.current === null) frame.current = requestAnimationFrame(flush);
    };
  };

  useEffect(() => () => source.current?.close(), []);

  const run = async () => {
    setError(null);
    try {
      const { runId: id } = await startRun(repo.trim(), mode);
      attach(id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not start the run");
    }
  };

  const shown = hideDeltas ? rows.filter((r) => r.type !== "model.message.delta") : rows;
  const field = "h-10 rounded-full border border-line bg-card px-4 text-[0.88rem] text-fg outline-none focus:border-line-strong";

  return (
    <main className="relative z-10 mx-auto flex h-dvh max-w-6xl flex-col px-6 py-6">
      <header className="flex flex-wrap items-center gap-3">
        <Link href="/" className="font-display text-[1.05rem] font-semibold text-fg">
          Greenlight <span className="text-fg-subtle">raw events</span>
        </Link>
        <span className="ml-auto font-mono text-[0.8rem] text-fg-muted">
          {runId ? `run ${runId}` : "no run"}  {status}  {total} events
        </span>
      </header>

      <form
        className="mt-5 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          run();
        }}
      >
        <label className="sr-only" htmlFor="raw-repo">Repo link</label>
        <input id="raw-repo" value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="https://github.com/owner/repo" className={`${field} min-w-80 flex-1 font-mono`} />
        <label className="sr-only" htmlFor="raw-mode">Mode</label>
        <select id="raw-mode" value={mode} onChange={(e) => setMode(e.target.value as Mode)} className={field}>
          <option value="pr_only">PR only</option>
          <option value="ship">Ship it</option>
        </select>
        <button type="submit" className="h-10 rounded-full bg-accent px-5 text-[0.88rem] font-semibold text-canvas">Run</button>
        <span className="mx-2 text-fg-subtle">or</span>
        <label className="sr-only" htmlFor="raw-attach">Run id</label>
        <input id="raw-attach" value={attachId} onChange={(e) => setAttachId(e.target.value)} placeholder="existing run id" className={`${field} w-72 font-mono`} />
        <button type="button" onClick={() => attachId.trim() && attach(attachId.trim())} className="h-10 rounded-full border border-line-strong px-5 text-[0.88rem] text-fg">
          Attach
        </button>
        <label className="ml-auto flex items-center gap-2 text-[0.85rem] text-fg-muted">
          <input type="checkbox" checked={hideDeltas} onChange={(e) => setHideDeltas(e.target.checked)} className="accent-[var(--accent)]" />
          Hide deltas
        </label>
      </form>
      {error && <p className="mt-3 text-[0.88rem] text-fail">{error}</p>}

      <ol className="mt-5 min-h-0 flex-1 overflow-y-auto rounded-[var(--radius-card)] border border-line bg-[#070707] p-3 font-mono text-[0.75rem] leading-relaxed">
        {shown.length === 0 && <li className="p-2 text-fg-subtle">Events print here as they arrive.</li>}
        {shown.map((r) => (
          <li key={r.seq} className="grid grid-cols-[4.5rem_13rem_minmax(0,1fr)] gap-3 border-b border-line/50 px-2 py-1 last:border-0">
            <span className="text-fg-subtle">#{r.seq}</span>
            <span className="text-accent">{r.type}</span>
            <span className="truncate text-fg-muted" title={r.data}>{r.data}</span>
          </li>
        ))}
      </ol>
    </main>
  );
}

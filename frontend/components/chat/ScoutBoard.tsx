"use client";

import { useEffect, useState } from "react";
import { FleetRow, SeverityChips } from "@/components/features/fleet/FleetRow";
import { exposed, type Summary } from "@/components/features/fleet/sweep";
import { ApiError, fetchScoutBoard } from "@/lib/api";

const RETRY_MS = 3000;
// The board is parsed from the scout's final message a moment after its turn ends.
const GIVE_UP_MS = 30_000;

type Phase = { kind: "waiting" } | { kind: "ready"; board: Summary } | { kind: "missing" } | { kind: "failed"; message: string };

/** The scout agent's ranked board as fleet rows, once its run has finished. */
export function ScoutBoard({ runId, finished, onFix }: { runId: string; finished: boolean; onFix: (repo: string) => void }) {
  const [phase, setPhase] = useState<Phase>({ kind: "waiting" });

  useEffect(() => {
    if (!finished) return;
    const controller = new AbortController();
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attempt = async () => {
      try {
        const board = await fetchScoutBoard(runId, controller.signal);
        if (controller.signal.aborted) return;
        if (board) {
          setPhase({ kind: "ready", board });
          return;
        }
      } catch (e) {
        if (controller.signal.aborted) return;
        setPhase({ kind: "failed", message: e instanceof ApiError ? e.message : "Could not load the scout's board" });
        return;
      }
      if (Date.now() - started > GIVE_UP_MS) setPhase({ kind: "missing" });
      else timer = setTimeout(attempt, RETRY_MS);
    };
    void attempt();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId, finished]);

  if (phase.kind === "waiting") return null;
  if (phase.kind === "missing") return <p className="text-[0.88rem] text-fg-muted">The scout finished without a board.</p>;
  if (phase.kind === "failed") return <p className="text-[0.88rem] text-fg-muted">{phase.message}</p>;

  const { board } = phase;
  const exposedCount = board.repos.filter(exposed).length;
  const failed = board.totals.error;
  return (
    <section aria-label="Scout board" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-[var(--radius-card)] border border-line bg-panel/80 px-4 py-3">
        <p className="text-[0.88rem] text-fg">
          <span className="font-mono tabular-nums">{board.totals.repos}</span> {board.totals.repos === 1 ? "repo" : "repos"} checked,{" "}
          {/* Clean only when every repo was actually scanned. */}
          <span className={exposedCount > 0 ? "text-fail" : failed > 0 ? "text-fg" : "text-accent"}>
            <span className="font-mono tabular-nums">{exposedCount}</span> exposed
          </span>
          {failed > 0 && (
            <>
              ,{" "}
              <span className="text-fail">
                <span className="font-mono tabular-nums">{failed}</span> could not be scanned
              </span>
            </>
          )}
        </p>
        <SeverityChips counts={board.totals.counts} />
      </div>
      {board.repos.length === 0 ? (
        <p className="text-[0.88rem] text-fg-muted">The scout found no repos to rank.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {board.repos.map((row) => (
            <FleetRow key={row.repo} row={row} onFix={onFix} />
          ))}
        </ul>
      )}
    </section>
  );
}

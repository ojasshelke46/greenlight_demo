"use client";

import { ArrowRight, Binoculars, CircleNotch, Play, Warning } from "@phosphor-icons/react";
import clsx from "clsx";
import { useCallback, useEffect, useState } from "react";
import { FleetRow, SeverityChips } from "@/components/features/fleet/FleetRow";
import { exposed, readEvents, SEVERITIES, type Counts, type RepoResult } from "@/components/features/fleet/sweep";
import { ApiError } from "@/lib/api";
import { campaignApi, usePolled, type FleetCampaign, type FleetItem } from "@/lib/campaigns";

const QUEUE_STATUS: Record<FleetItem["status"], { label: string; className: string }> = {
  queued: { label: "Queued", className: "text-fg-subtle" },
  running: { label: "In progress", className: "text-progress" },
  done: { label: "Done", className: "text-accent" },
};

const busy = (f: FleetCampaign) => f.status === "scanning" || f.active !== null;
const loadFleet = (id: string) => campaignApi.getFleet(id);

function totals(rows: RepoResult[]): Counts {
  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, 0])) as Counts;
  for (const row of rows) for (const s of SEVERITIES) counts[s] += row.counts[s];
  return counts;
}

/** Scout: the board fills one repo at a time as each scan finishes, then the exposed repos queue by risk. */
export function FleetCampaignView({ campaignId, onOpenCampaign }: { campaignId: string; onOpenCampaign: (campaignId: string) => void }) {
  const [streamed, setStreamed] = useState<{ id: string; rows: RepoResult[] }>({ id: campaignId, rows: [] });
  const [streamError, setStreamError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const { value: fleet, refresh } = usePolled(campaignId, loadFleet, busy);

  // The event stream delivers each repo as its scan finishes; the poll above keeps the queue's statuses fresh.
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch(`/api/campaigns/fleet/${encodeURIComponent(campaignId)}/events`, { headers: { Accept: "text/event-stream" }, cache: "no-store", signal: controller.signal });
        if (!response.ok || !response.body) {
          setStreamError(`The fleet stream could not start (${response.status})`);
          return;
        }
        for await (const { event, data } of readEvents(response.body)) {
          if (event === "repo") {
            const row = JSON.parse(data) as RepoResult;
            setStreamed((s) => (s.id === campaignId ? { id: campaignId, rows: [...s.rows.filter((r) => r.repo !== row.repo), row] } : { id: campaignId, rows: [row] }));
          } else if (event === "queue") {
            refresh();
          }
        }
      } catch {
        if (!controller.signal.aborted) setStreamError("Lost the fleet stream. The results below come from the last update.");
      }
    })();
    return () => controller.abort();
  }, [campaignId, refresh]);

  const rows = fleet && fleet.status !== "scanning" ? fleet.repos : streamed.id === campaignId ? streamed.rows : [];
  const startNext = useCallback(async () => {
    setStarting(true);
    setActionError(null);
    try {
      const { campaign_id } = await campaignApi.fleetNext(campaignId);
      onOpenCampaign(campaign_id);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "Could not start the next repo");
    } finally {
      setStarting(false);
      refresh();
    }
  }, [campaignId, onOpenCampaign, refresh]);

  const scanning = !fleet || fleet.status === "scanning";
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-5 px-5 pb-10 pt-8">
      <header className="flex items-center gap-3">
        <span className="grid size-10 place-items-center rounded-full border border-line-strong bg-card text-fg-muted">
          <Binoculars weight="bold" className="size-5" aria-hidden />
        </span>
        <div className="min-w-0">
          <h2 className="font-display text-[1.1rem] font-semibold text-fg">Sweep {fleet?.target ?? "the fleet"}</h2>
          <p className="text-[0.85rem] text-fg-muted" aria-live="polite">
            {scanning ? `Scanning one repo at a time. ${rows.length} done so far.` : fleet?.status === "failed" ? "The sweep stopped." : `${rows.length} repos scanned, ${rows.filter(exposed).length} exposed.`}
          </p>
        </div>
        {scanning && <CircleNotch weight="bold" className="ml-auto size-5 animate-spin text-progress motion-reduce:animate-none" aria-hidden />}
      </header>

      {(fleet?.error || streamError) && (
        <p role="alert" className="flex items-start gap-2 rounded-xl border border-fail/30 bg-fail/10 px-4 py-3 text-[0.88rem] text-fail">
          <Warning weight="bold" className="mt-0.5 size-4 shrink-0" aria-hidden />
          {fleet?.error ?? streamError}
        </p>
      )}

      {rows.length > 0 && <SeverityChips counts={totals(rows)} />}
      <ul className="flex flex-col gap-2">
        {rows.map((row) => (
          <FleetRow key={row.repo} row={row} fixable={false} />
        ))}
      </ul>

      {fleet && fleet.status === "ready" && (
        <section className="rounded-[var(--radius-card)] border border-line bg-panel/80" aria-label="Fix queue">
          <header className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-3">
            <h3 className="font-display text-[0.98rem] font-semibold text-fg">{fleet.queue.length === 0 ? "No exposed repos" : `Fix queue, riskiest first`}</h3>
            {fleet.queue.length > 0 && (
              <button
                type="button"
                disabled={fleet.next === null || fleet.active !== null || starting}
                onClick={startNext}
                className="ml-auto inline-flex h-9 items-center gap-1.5 rounded-full bg-accent px-4 text-[0.85rem] font-semibold text-canvas transition-[opacity,transform] duration-150 ease-out active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40"
              >
                {starting ? <CircleNotch weight="bold" className="size-4 animate-spin motion-reduce:animate-none" aria-hidden /> : <Play weight="fill" className="size-4" aria-hidden />}
                Fix next repo
              </button>
            )}
          </header>
          {actionError && <p role="alert" className="border-b border-line px-4 py-2.5 text-[0.85rem] text-fail">{actionError}</p>}
          <ol>
            {fleet.queue.map((item) => (
              <li key={item.repo} className="flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0">
                <span className="min-w-0 flex-1 truncate font-mono text-[0.88rem] text-fg">{item.repo}</span>
                <span className="font-mono text-[0.78rem] text-fg-subtle">risk {item.risk}</span>
                <span className={clsx("text-[0.8rem] font-medium", QUEUE_STATUS[item.status].className)}>
                  {QUEUE_STATUS[item.status].label}
                  {item.total !== undefined && item.status !== "queued" ? `, ${item.done} of ${item.total} fixed` : ""}
                </span>
                {item.campaign_id && (
                  <button type="button" onClick={() => onOpenCampaign(item.campaign_id!)} className="inline-flex h-8 items-center gap-1 rounded-full border border-line-strong px-3 text-[0.8rem] text-fg transition-colors duration-150 hover:border-accent/50 hover:text-accent">
                    Open
                    <ArrowRight weight="bold" className="size-3.5" aria-hidden />
                  </button>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

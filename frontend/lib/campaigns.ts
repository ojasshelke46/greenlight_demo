"use client";

// Campaigns: scan first, then one package (or one repo, or one policy) at a time. Every status comes from the
// backend, which derives it from each fix run, its PR and its approvals.

import { useCallback, useEffect, useState } from "react";
import type { RepoResult } from "@/components/features/fleet/sweep";
import { ApiError } from "./api";
import type { AccessInfo, Mode } from "./state";

export type ItemStatus = "queued" | "fixing" | "pr_opened" | "awaiting_approval" | "merged" | "failed" | "skipped";

export type CampaignItem = {
  package: string;
  from: string | null;
  to: string | null;
  advisories: string[];
  severity: string | null;
  status: ItemStatus;
  run_id: string | null;
  pr_url: string | null;
  pr_number: number | null;
  reason: string | null;
};

export type Campaign = {
  id: string;
  repo: string;
  mode: Mode;
  auto: boolean;
  scan_run_id: string;
  scan_status: "running" | "done" | "failed";
  items: CampaignItem[];
  next: CampaignItem | null;
  active: CampaignItem | null;
  done: number;
  total: number;
  stopped: { package: string; run_id: string; reason: string } | null;
  // Seconds until the next item starts on its own (auto mode), or null.
  next_start_in: number | null;
};

export type FleetItem = { repo: string; risk: number; status: "queued" | "running" | "done"; campaign_id: string | null; done?: number; total?: number };
export type FleetCampaign = {
  id: string;
  target: string;
  mode: Mode;
  status: "scanning" | "ready" | "failed";
  repos: RepoResult[];
  queue: FleetItem[];
  error: string | null;
  next: FleetItem | null;
  active: FleetItem | null;
};

export type PolicyItem = { repo: string; status: "queued" | "drafting" | "pr_opened" | "failed" | "skipped"; run_id: string | null; pr_url: string | null; reason: string | null };
export type PolicyCampaign = {
  id: string;
  label: string;
  auto: boolean;
  status: "running" | "done";
  stopped: { repo: string; reason: string } | null;
  items: PolicyItem[];
  next: PolicyItem | null;
  active: PolicyItem | null;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/campaigns${path}`, {
    cache: "no-store",
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`, response.status);
  return body as T;
}

type BackendAccess = { repo: string; private: boolean; default_branch: string; mode_options: Mode[]; via_fork: boolean };

export async function startCampaign(repoUrl: string, mode: Mode, auto = false): Promise<{ campaignId: string; access: AccessInfo }> {
  const body = await call<{ campaign_id: string; access: BackendAccess }>("", { method: "POST", body: JSON.stringify({ repo: repoUrl, mode, auto }) });
  const a = body.access;
  return {
    campaignId: body.campaign_id,
    access: { repo: a.repo, private: a.private, defaultBranch: a.default_branch, collaborator: !a.via_fork, viaFork: a.via_fork, modeOptions: a.mode_options },
  };
}

export const campaignApi = {
  get: (id: string) => call<Campaign>(`/${id}`),
  next: (id: string) => call<{ run_id: string }>(`/${id}/next`, { method: "POST", body: "{}" }),
  fix: (id: string, pkg: string) => call<{ run_id: string }>(`/${id}/items/${pkg.split("/").map(encodeURIComponent).join("/")}/fix`, { method: "POST", body: "{}" }),
  skip: (id: string, pkg: string) => call<Campaign>(`/${id}/items/${pkg.split("/").map(encodeURIComponent).join("/")}/skip`, { method: "POST", body: "{}" }),
  auto: (id: string, auto: boolean) => call<Campaign>(`/${id}/auto`, { method: "POST", body: JSON.stringify({ auto }) }),
  stop: (id: string) => call<Campaign>(`/${id}/stop`, { method: "POST", body: "{}" }),
  startFleet: (target: string, mode: Mode) => call<{ campaign_id: string }>("/fleet", { method: "POST", body: JSON.stringify({ target, mode }) }),
  getFleet: (id: string) => call<FleetCampaign>(`/fleet/${id}`),
  fleetNext: (id: string) => call<{ campaign_id: string }>(`/fleet/${id}/next`, { method: "POST", body: "{}" }),
  startPolicy: (repos: string[]) => call<{ campaign_id: string }>("/policy", { method: "POST", body: JSON.stringify({ repos, auto: true }) }),
  getPolicy: (id: string) => call<PolicyCampaign>(`/policy/${id}`),
  policyNext: (id: string) => call<{ run_id: string | null }>(`/policy/${id}/next`, { method: "POST", body: "{}" }),
  policyStop: (id: string) => call<PolicyCampaign>(`/policy/${id}/stop`, { method: "POST", body: "{}" }),
  list: () => call<{ id: string; kind: "fleet" | "policy"; label: string | null; status: string | null; created_at: string }[]>(""),
};

const LIVE_MS = 2000;
const IDLE_MS = 6000;

/** Polls a campaign: every 2 seconds while something is running or about to start, every 6 otherwise. */
export function usePolled<T>(id: string | null, load: (id: string) => Promise<T>, busy: (value: T) => boolean): { value: T | null; error: string | null; refresh: () => void } {
  const [state, setState] = useState<{ id: string | null; value: T | null; error: string | null }>({ id: null, value: null, error: null });
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!id) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      let next = IDLE_MS;
      try {
        const value = await load(id);
        if (stopped) return;
        setState({ id, value, error: null });
        next = busy(value) ? LIVE_MS : IDLE_MS;
      } catch (e) {
        if (stopped) return;
        setState((s) => ({ ...s, id, error: e instanceof ApiError ? e.message : "Could not load the campaign" }));
      }
      timer = setTimeout(run, next);
    };
    void run();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [id, load, busy, tick]);

  return { value: state.id === id ? state.value : null, error: state.id === id ? state.error : null, refresh };
}

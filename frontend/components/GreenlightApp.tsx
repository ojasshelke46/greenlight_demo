"use client";

import { Tooltip } from "@base-ui/react/tooltip";
import { Notebook } from "@phosphor-icons/react";
import clsx from "clsx";
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Conversation } from "@/components/chat/Conversation";
import { Composer, type ComposerNote } from "@/components/composer/Composer";
import { Hero, Suggestions } from "@/components/EmptyState";
import { LedgerDrawer, PolicySheet } from "@/components/shell/Sheets";
import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { ApiError, fetchAccess, fetchAgents, fetchHealth, fetchRun, fetchRuns, startAgentRun, type StartRequest } from "@/lib/api";
import { useApproval } from "@/lib/features/approval";
import { usePolicy } from "@/lib/features/policy";
import { findRepoUrl, type AccessState, type AgentInfo, type ChatRole, type Mode, type RunMeta, type RunSummary } from "@/lib/state";
import { useRun } from "@/lib/use-run";

const USER_NAME = process.env.NEXT_PUBLIC_USER_NAME || "there";
const DEMO_REPO = process.env.NEXT_PUBLIC_DEMO_REPO || "";
const DEMO_OWNER = findRepoUrl(DEMO_REPO)?.repo.split("/")[0] ?? "";

/** The backend's key for a scout target: org:name and user:name as typed, repo links as repos:owner/name,… */
function scoutKey(target: string): string {
  if (OWNER_TARGET.test(target)) return target.toLowerCase();
  return `repos:${target.split(",").map((url) => findRepoUrl(url)?.repo ?? url).join(",")}`;
}
const ACCESS_DEBOUNCE_MS = 350;
const POLICY_MAX_AGE_MS = 15_000;
const OWNER_TARGET = /^(org|user):([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))$/i;
const REPO_URLS = /https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]+/g;

/** A scout target from free text: org:name, user:name, or every GitHub repo link in it, comma separated. */
function scoutTarget(text: string): string | null {
  const trimmed = text.trim();
  if (OWNER_TARGET.test(trimmed)) return trimmed;
  const repos = Array.from(new Set((trimmed.match(REPO_URLS) ?? []).map((url) => findRepoUrl(url)?.url).filter((u): u is string => Boolean(u))));
  return repos.length > 0 ? repos.join(",") : null;
}

// Hero and suggestions leave by fading and lifting: 220ms strong ease out.
const LEAVE = { opacity: 0, transform: "translateY(-12px)" };
const LEAVE_T = { duration: 0.22, ease: [0.23, 1, 0.32, 1] } as const;
// The composer glides to the bottom: movement on screen, strong ease in out, 350ms.
const GLIDE = { layout: { duration: 0.35, ease: [0.77, 0, 0.175, 1] } } as const;

function setUrl(params: Record<string, string> | null) {
  const url = new URL(window.location.href);
  url.search = params ? new URLSearchParams(params).toString() : "";
  window.history.replaceState(null, "", url);
}

export function GreenlightApp() {
  const reduce = useReducedMotion();
  const { run, open, decide, pause, resume, say, reset } = useRun();
  const { submitApproval } = useApproval();
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const follow = useRef(true);

  const [text, setText] = useState("");
  const [checked, setChecked] = useState<{ url: string; state: AccessState } | null>(null);
  const [mode, setMode] = useState<Mode>("ship");
  const [agent, setAgent] = useState<ChatRole>("fixer");
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  const [reportRunId, setReportRunId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [answering, setAnswering] = useState(false);
  const [controlError, setControlError] = useState<string | null>(null);
  const [flare, setFlare] = useState(false);
  const [replay, setReplay] = useState(false);
  // True while a run named in the URL is loading, so the empty state does not flash first.
  const [restoring, setRestoring] = useState(false);

  // Desktop keeps the sidebar docked; below lg it is an overlay that starts closed.
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [policyOpen, setPolicyOpen] = useState(false);
  const [ledgerRunId, setLedgerRunId] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [trueforge, setTrueforge] = useState<boolean | null>(null);

  const found = findRepoUrl(text);
  const repoUrl = found?.url ?? null;
  const target = agent === "scout" ? scoutTarget(text) : null;
  const policyRepo = agent === "policy" ? (found?.repo ?? null) : null;
  const policy = usePolicy(policyRepo, POLICY_MAX_AGE_MS);

  // Access check for Fix: debounced while typing, aborted when the link changes again.
  useEffect(() => {
    if (!repoUrl || agent !== "fixer") return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetchAccess(repoUrl, controller.signal)
        .then((info) => {
          setChecked({ url: repoUrl, state: { status: "ready", access: info } });
          setMode((m) => (info.modeOptions.includes(m) ? m : info.modeOptions[0] ?? "pr_only"));
        })
        .catch((error) => {
          if (controller.signal.aborted) return;
          setChecked({ url: repoUrl, state: { status: "error", message: error instanceof ApiError ? error.message : "Could not check access" } });
        });
    }, ACCESS_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [repoUrl, agent]);

  const access: AccessState = !found
    ? { status: "idle" }
    : checked?.url === repoUrl
      ? checked.state
      : { status: "checking", repo: found.repo };

  useEffect(() => {
    fetchAgents()
      .then(setAgents)
      .catch(() => setAgents([]));
  }, []);

  const refreshRuns = useCallback(() => {
    fetchRuns()
      .then((list) => {
        setRuns(list);
        setRunsError(null);
      })
      .catch((e: Error) => setRunsError(e.message));
  }, []);

  useEffect(() => {
    refreshRuns();
    const checkHealth = () => fetchHealth().then((h) => setTrueforge(h.trueforge)).catch(() => setTrueforge(false));
    checkHealth();
    const runsTimer = setInterval(refreshRuns, 8000);
    const healthTimer = setInterval(checkHealth, 15000);
    return () => {
      clearInterval(runsTimer);
      clearInterval(healthTimer);
    };
  }, [refreshRuns]);

  const restore = useCallback(
    async (runId: string, isReplay: boolean) => {
      try {
        const r = await fetchRun(runId);
        // Only fix runs have a repo to check access for; a scout run's repo is its target.
        const info = r.role === "scout" ? null : await fetchAccess(`https://github.com/${r.repo}`).catch(() => null);
        const meta: RunMeta = {
          id: r.id,
          role: r.role ?? "fixer",
          repo: r.repo,
          mode: r.mode,
          viaFork: r.via_fork,
          private: info?.private ?? null,
          defaultBranch: info?.defaultBranch ?? null,
        };
        setReplay(isReplay);
        setUrl(isReplay ? { run: runId, replay: "1" } : { run: runId });
        follow.current = true;
        open(meta);
      } catch (e) {
        setSendError(e instanceof Error ? e.message : "Could not open that run");
      }
    },
    [open],
  );

  // A run in the URL survives a reload; ?replay=1 marks a replayed run. Without a run, ?repo=
  // (a repo link or owner/repo) prefills the launch composer.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const runId = params.get("run");
    if (!runId) {
      const repo = params.get("repo")?.trim();
      if (repo) {
        const url = findRepoUrl(repo)?.url ?? (/^[\w.-]+\/[\w.-]+$/.test(repo) ? `https://github.com/${repo}` : repo);
        // Reading the URL once on mount is syncing with an external system.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setText(url);
      }
      return;
    }
    setRestoring(true);
    restore(runId, params.get("replay") === "1").finally(() => setRestoring(false));
  }, [restore]);

  const launch = useCallback(
    async (request: StartRequest) => {
      setSendError(null);
      const { runId, role, access: info } = await startAgentRun(request);
      setReplay(false);
      setUrl({ run: runId });
      follow.current = true;
      const repo = info?.repo ?? (request.role === "scout" ? scoutKey(request.target) : (findRepoUrl(request.repo)?.repo ?? request.repo));
      open({
        id: runId,
        role,
        repo,
        mode: request.role === "fixer" ? request.mode : "pr_only",
        viaFork: info?.viaFork ?? false,
        private: info?.private ?? null,
        defaultBranch: info?.defaultBranch ?? null,
      });
      refreshRuns();
    },
    [open, refreshRuns],
  );

  const ready = access.status === "ready" ? access.access : null;
  const policyReady = policy?.status === "ready" ? policy.info : null;
  const request = (): StartRequest | null => {
    if (agent === "scout") return target ? { role: "scout", target } : null;
    if (!repoUrl) return null;
    return agent === "policy" ? { role: "policy", repo: repoUrl } : { role: "fixer", repo: repoUrl, mode };
  };

  // With a run open, anything that is not a new run request (no repo link, no scout target) goes to that
  // run's agent in the same session: "hi", "continue", or the answer to its question.
  const followUp = run !== null && text.trim() !== "" && request() === null;
  const awaitingMerge = run?.approval.status === "pending" || run?.approval.status === "refused";
  const followUpBlocked = !followUp ? null : awaitingMerge ? "Approve or reject the merge first" : null;
  const followUpNote = !followUp
    ? null
    : (followUpBlocked ?? (run?.question ? "Sends your answer to the agent" : "Sends to this run's agent, in the same session"));

  const canSend =
    !sending &&
    (followUp
      ? followUpBlocked === null
      : agent === "fixer"
      ? Boolean(repoUrl && ready && ready.modeOptions.includes(mode))
      : agent === "scout"
        ? target !== null
        : Boolean(repoUrl && policyReady && !policyReady.exists));

  const note: ComposerNote | null =
    agent === "scout"
      ? text.trim() === ""
        ? null
        : target
          ? { tone: "ok", text: OWNER_TARGET.test(target) ? `Scout every repo of ${target.replace(":", " ")}` : `Scout ${target.split(",").length} ${target.includes(",") ? "repos" : "repo"}` }
          : { tone: "muted", text: "Try org:name, user:name, or GitHub repo links" }
      : agent === "policy"
        ? !found
          ? text.trim() === "" ? null : { tone: "muted", text: "Paste a GitHub repo link to draft its policy" }
          : !policy || policy.status === "loading"
            ? { tone: "muted", text: `Checking ${found.repo} for .greenlight.yml` }
            : policy.status === "failed"
              ? { tone: "error", text: policy.message }
              : policy.info.exists
                ? { tone: "error", text: `${found.repo} already has ${policy.info.path}` }
                : { tone: "ok", text: `No ${policy.info.path} in ${found.repo} yet. The policy agent drafts one` }
        : null;

  const send = async () => {
    if (!canSend) return;
    if (followUp) {
      setSending(true);
      setSendError(null);
      follow.current = true;
      const error = await say(text.trim());
      if (error) setSendError(error);
      else setText("");
      setSending(false);
      refreshRuns();
      return;
    }
    const next = request();
    if (!next) return;
    setSending(true);
    try {
      await launch(next);
      setText("");
    } catch (e) {
      setSendError(e instanceof ApiError ? e.message : "Could not start the run");
    } finally {
      setSending(false);
    }
  };

  /** "Fix this repo" on the scout's board: the same access check as the composer, then a Fix run. */
  const fixRepo = async (repo: string) => {
    const url = `https://github.com/${repo}`;
    try {
      const info = await fetchAccess(url);
      const fixMode: Mode = info.modeOptions.includes(mode) ? mode : (info.modeOptions[0] ?? "pr_only");
      setAgent("fixer");
      await launch({ role: "fixer", repo: url, mode: fixMode });
    } catch (e) {
      setSendError(e instanceof ApiError ? e.message : `Could not start a Fix run on ${repo}`);
    }
  };

  const retry = async () => {
    if (!run) return;
    setRetrying(true);
    try {
      await launch(
        run.role === "scout"
          ? { role: "scout", target: run.repo.replace(/^repos:/, "") }
          : run.role === "policy"
            ? { role: "policy", repo: `https://github.com/${run.repo}` }
            : { role: "fixer", repo: `https://github.com/${run.repo}`, mode: run.mode },
      );
    } catch (e) {
      setSendError(e instanceof ApiError ? e.message : "Could not start the run");
    } finally {
      setRetrying(false);
    }
  };

  const onAnswer = async (answer: string) => {
    setAnswering(true);
    setControlError(null);
    follow.current = true;
    const error = await say(answer);
    if (error) setControlError(error);
    setAnswering(false);
    refreshRuns();
  };

  const onPause = async () => {
    setControlError(null);
    const error = await pause();
    if (error) setSendError(error);
    refreshRuns();
  };

  const onResume = async () => {
    setResuming(true);
    setControlError(null);
    const error = await resume();
    if (error) setControlError(error);
    setResuming(false);
    refreshRuns();
  };

  const onDecide = (decision: "approve" | "reject") => {
    if (decision === "approve") {
      // The orb flares once when the hold completes, then the decision goes to the backend.
      setFlare(true);
      setTimeout(() => setFlare(false), 800);
    }
    decide(decision, submitApproval).then(refreshRuns);
  };

  const newRun = () => {
    reset();
    setReplay(false);
    setUrl(null);
    setText("");
    setSendError(null);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const pickSuggestion = (suggested: ChatRole) => {
    setAgent(suggested);
    setSendError(null);
    setText(suggested === "scout" ? `user:${DEMO_OWNER}` : DEMO_REPO || "https://github.com/");
    requestAnimationFrame(() => {
      const el = composerRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  };

  // Keep the newest agent output in view unless the reader scrolled up.
  const eventCount = run?.eventCount ?? 0;
  const approvalStatus = run?.approval.status;
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [eventCount, approvalStatus]);

  const hasRun = run !== null;
  const showEmpty = !hasRun && !restoring;

  return (
    <Tooltip.Provider delay={300}>
      <div className="relative z-10 flex h-dvh overflow-hidden">
        {mobileSidebar && <button type="button" aria-label="Close sidebar" onClick={() => setMobileSidebar(false)} className="fixed inset-0 z-30 bg-black/55 lg:hidden" />}
        {(
          <Sidebar
            className={clsx(mobileSidebar ? "max-lg:flex" : "max-lg:hidden", sidebarOpen ? "lg:flex" : "lg:hidden")}
            runs={runs}
            runsError={runsError}
            activeRunId={run?.id ?? null}
            onSelect={(id) => {
              setMobileSidebar(false);
              restore(id, true);
            }}
            onNew={() => {
              setMobileSidebar(false);
              newRun();
            }}
            onCollapse={() => {
              setSidebarOpen(false);
              setMobileSidebar(false);
            }}
            onOpenPolicy={() => setPolicyOpen(true)}
            onOpenLedger={(id) => setLedgerRunId(id)}
          />
        )}

        <div className="relative flex min-w-0 flex-1 flex-col">
          <TopBar
            sidebarOpen={sidebarOpen}
            onOpenSidebar={() => {
              setSidebarOpen(true);
              setMobileSidebar(true);
            }} replay={replay} trueforge={trueforge} userName={USER_NAME} />

          <LayoutGroup>
            <div className={clsx("flex min-h-0 flex-1 flex-col", showEmpty && "justify-center pb-16", restoring && "justify-end")}>
              <AnimatePresence mode="popLayout" initial={false}>
                {showEmpty && (
                  <motion.div key="hero" exit={reduce ? { opacity: 0 } : LEAVE} transition={LEAVE_T}>
                    <Hero userName={USER_NAME} />
                  </motion.div>
                )}
              </AnimatePresence>

              {hasRun && (
                <div
                  ref={scroller}
                  onScroll={(e) => {
                    const el = e.currentTarget;
                    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
                  }}
                  className="min-h-0 flex-1 overflow-y-auto"
                >
                  <Conversation
                    run={run}
                    flare={flare}
                    retrying={retrying}
                    resuming={resuming}
                    controlError={controlError}
                    onDecide={onDecide}
                    onRetry={retry}
                    onResume={onResume}
                    onAuditReport={(auditorRunId) => {
                      setReportRunId(auditorRunId);
                      setLedgerRunId(run.id);
                    }}
                    onFixRepo={fixRepo}
                    onAnswer={onAnswer}
                    answering={answering}
                  />
                </div>
              )}

              <motion.div layout="position" transition={GLIDE} className={clsx("mx-auto w-full max-w-3xl px-5", showEmpty ? "mt-10" : "pb-5 pt-2")}>
                <Composer
                  ref={composerRef}
                  agent={agent}
                  onAgentChange={(next) => {
                    setAgent(next);
                    setSendError(null);
                  }}
                  agents={agents}
                  note={note}
                  followUpNote={followUpNote}
                  hasRun={hasRun}
                  text={text}
                  onTextChange={(t) => {
                    setText(t);
                    setSendError(null);
                  }}
                  hasRepo={repoUrl !== null}
                  access={access}
                  mode={mode}
                  onModeChange={setMode}
                  canSend={canSend}
                  sending={sending}
                  error={sendError}
                  onSend={send}
                  agentWorking={Boolean(run?.status.working)}
                  pausing={Boolean(run?.pausing)}
                  onPause={onPause}
                />
              </motion.div>

              <AnimatePresence mode="popLayout" initial={false}>
                {showEmpty && (
                  <motion.div key="suggestions" exit={reduce ? { opacity: 0 } : LEAVE} transition={LEAVE_T} className="mt-4">
                    <Suggestions onPick={pickSuggestion} />
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </LayoutGroup>

          {hasRun && (
            <button
              type="button"
              onClick={() => setLedgerRunId(run.id)}
              className="absolute bottom-7 right-6 hidden h-9 items-center gap-2 rounded-full border border-line-strong bg-panel/70 px-4 text-[0.8rem] text-fg-muted backdrop-blur-sm transition-colors duration-150 hover:border-accent/40 hover:text-fg 2xl:inline-flex"
            >
              <Notebook weight="bold" className="size-4" aria-hidden />
              Open audit ledger
            </button>
          )}
        </div>
      </div>

      <PolicySheet
        open={policyOpen}
        onOpenChange={setPolicyOpen}
        run={run}
        onOpenRun={(id) => {
          setPolicyOpen(false);
          refreshRuns();
          restore(id, false);
        }}
      />
      <LedgerDrawer
        runId={ledgerRunId}
        reportRunId={reportRunId}
        open={ledgerRunId !== null}
        onOpenChange={(o) => {
          if (o) return;
          setLedgerRunId(null);
          setReportRunId(null);
        }}
      />
    </Tooltip.Provider>
  );
}

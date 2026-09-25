"use client";

import { Tooltip } from "@base-ui/react/tooltip";
import { Notebook } from "@phosphor-icons/react";
import clsx from "clsx";
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Conversation } from "@/components/chat/Conversation";
import { Composer } from "@/components/composer/Composer";
import { Hero, Suggestions } from "@/components/EmptyState";
import { LedgerDrawer, PolicySheet } from "@/components/shell/Sheets";
import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { ApiError, fetchAccess, fetchHealth, fetchRun, fetchRuns, startRun } from "@/lib/api";
import { findRepoUrl, type AccessState, type Mode, type RunMeta, type RunSummary } from "@/lib/state";
import { useRun } from "@/lib/use-run";

const USER_NAME = process.env.NEXT_PUBLIC_USER_NAME || "there";
const DEMO_REPO = process.env.NEXT_PUBLIC_DEMO_REPO || "";
const ACCESS_DEBOUNCE_MS = 350;

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
  const { run, open, decide, pause, resume, reset } = useRun();
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const follow = useRef(true);

  const [text, setText] = useState("");
  const [checked, setChecked] = useState<{ url: string; state: AccessState } | null>(null);
  const [mode, setMode] = useState<Mode>("ship");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [resuming, setResuming] = useState(false);
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

  // Access check: debounced while typing, aborted when the link changes again.
  useEffect(() => {
    if (!repoUrl) return;
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
  }, [repoUrl]);

  const access: AccessState = !found
    ? { status: "idle" }
    : checked?.url === repoUrl
      ? checked.state
      : { status: "checking", repo: found.repo };

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
        const info = await fetchAccess(`https://github.com/${r.repo}`).catch(() => null);
        const meta: RunMeta = {
          id: r.id,
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

  // A run in the URL survives a reload; ?replay=1 marks a replayed run.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const runId = params.get("run");
    if (!runId) return;
    // Reading the URL once on mount is syncing with an external system.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRestoring(true);
    restore(runId, params.get("replay") === "1").finally(() => setRestoring(false));
  }, [restore]);

  const launch = useCallback(
    async (url: string, runMode: Mode) => {
      setSendError(null);
      const { runId, access: info } = await startRun(url, runMode);
      setReplay(false);
      setUrl({ run: runId });
      follow.current = true;
      open({ id: runId, repo: info.repo, mode: runMode, viaFork: info.viaFork, private: info.private, defaultBranch: info.defaultBranch });
      refreshRuns();
    },
    [open, refreshRuns],
  );

  const ready = access.status === "ready" ? access.access : null;
  const canSend = Boolean(repoUrl && ready && ready.modeOptions.includes(mode) && !sending);

  const send = async () => {
    if (!canSend || !repoUrl) return;
    setSending(true);
    try {
      await launch(repoUrl, mode);
      setText("");
    } catch (e) {
      setSendError(e instanceof ApiError ? e.message : "Could not start the run");
    } finally {
      setSending(false);
    }
  };

  const retry = async () => {
    if (!run) return;
    setRetrying(true);
    try {
      await launch(`https://github.com/${run.repo}`, run.mode);
    } catch (e) {
      setSendError(e instanceof ApiError ? e.message : "Could not start the run");
    } finally {
      setRetrying(false);
    }
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
    decide(decision, USER_NAME).then(refreshRuns);
  };

  const newRun = () => {
    reset();
    setReplay(false);
    setUrl(null);
    setText("");
    setSendError(null);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const pickSuggestion = (suggested: Mode | null) => {
    if (suggested) setMode(suggested);
    setText(DEMO_REPO || "https://github.com/");
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
                  <Conversation run={run} flare={flare} retrying={retrying} resuming={resuming} controlError={controlError} onDecide={onDecide} onRetry={retry} onResume={onResume} />
                </div>
              )}

              <motion.div layout="position" transition={GLIDE} className={clsx("mx-auto w-full max-w-3xl px-5", showEmpty ? "mt-10" : "pb-5 pt-2")}>
                <Composer
                  ref={composerRef}
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

      <PolicySheet open={policyOpen} onOpenChange={setPolicyOpen} />
      <LedgerDrawer runId={ledgerRunId} open={ledgerRunId !== null} onOpenChange={(o) => !o && setLedgerRunId(null)} />
    </Tooltip.Provider>
  );
}

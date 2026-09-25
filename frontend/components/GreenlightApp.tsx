"use client";

import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { useState } from "react";
import { LaunchView } from "@/components/launch/LaunchView";
import { RunView } from "@/components/run/RunView";
import { launchWithUrl, placeholderRun, placeholderState } from "@/lib/placeholder";
import type { GreenlightState, Mode } from "@/lib/state";

export function GreenlightApp() {
  // Single state object; swap for useReducer over real events without touching the views.
  const [state, setState] = useState<GreenlightState>(placeholderState);

  const setRepoUrl = (url: string) =>
    setState((s) => ({ ...s, launch: launchWithUrl(s.launch, url) }));

  const setMode = (mode: Mode) => setState((s) => ({ ...s, launch: { ...s.launch, mode } }));

  const startRun = () =>
    setState((s) => {
      const { access, mode } = s.launch;
      if (access.status !== "ready" || mode === null) return s;
      return { ...s, view: "run", run: placeholderRun(access.access, mode) };
    });

  const newRun = () => setState((s) => ({ ...s, view: "launch", run: null }));

  return (
    <MotionConfig reducedMotion="user">
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={state.view}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          {state.view === "run" && state.run ? (
            <RunView run={state.run} onNewRun={newRun} />
          ) : (
            <LaunchView launch={state.launch} onRepoUrlChange={setRepoUrl} onModeChange={setMode} onRun={startRun} />
          )}
        </motion.div>
      </AnimatePresence>
    </MotionConfig>
  );
}

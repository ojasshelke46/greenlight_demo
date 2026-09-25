"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { TrafficLight } from "@/components/TrafficLight";
import type { RunState, Signal } from "@/lib/state";
import { OutputPanel } from "./OutputPanel";
import { Pipeline } from "./Pipeline";
import { PolicyPanel } from "./PolicyPanel";
import { RunHeader } from "./RunHeader";

const SIGNAL_TEXT: Record<Signal, string> = {
  red: "text-signal-red",
  amber: "text-signal-amber",
  green: "text-signal-green",
};

export function RunView({ run, onNewRun }: { run: RunState; onNewRun: () => void }) {
  const reduce = useReducedMotion();
  const working = run.status.signal === "amber" && run.connection === "live";

  return (
    <div className="flex min-h-[100dvh] flex-col lg:h-[100dvh]">
      <RunHeader run={run} onNewRun={onNewRun} />

      <main className="grid flex-1 grid-cols-1 gap-8 px-4 py-6 md:px-8 lg:min-h-0 lg:grid-cols-[15rem_minmax(0,1fr)_19rem] lg:grid-rows-[auto_minmax(0,1fr)] lg:gap-x-10 lg:gap-y-8">
        <section
          aria-label="Run status"
          className="flex flex-col items-center gap-6 lg:row-span-2 lg:items-start"
        >
          <TrafficLight signal={run.status.signal} working={working} />
          <div className="text-center lg:text-left" aria-live="polite">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={`${run.status.signal}:${run.status.label}`}
                initial={reduce ? false : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
              >
                <p className={`text-3xl font-semibold leading-tight tracking-tight ${SIGNAL_TEXT[run.status.signal]}`}>
                  {run.status.label}
                </p>
                <p className="mt-2 text-lg leading-snug text-fg">{run.status.detail}</p>
              </motion.div>
            </AnimatePresence>
          </div>
        </section>

        <div className="lg:col-span-2">
          <Pipeline steps={run.steps} />
        </div>

        <OutputPanel run={run} />

        <PolicyPanel policy={run.policy} />
      </main>
    </div>
  );
}

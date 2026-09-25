"use client";

import { motion, useReducedMotion } from "motion/react";
import type { Signal } from "@/lib/state";

const LAMPS: Signal[] = ["red", "amber", "green"];

const LAMP_NAME: Record<Signal, string> = {
  red: "Red: vulnerable or blocked",
  amber: "Amber: agent working",
  green: "Green: tests passing or approved",
};

type Props = {
  signal: Signal | null;
  // Amber breathes only while the agent is actually working; a paused run holds steady.
  working?: boolean;
  size?: "lg" | "md";
};

export function TrafficLight({ signal, working = false, size = "lg" }: Props) {
  const reduce = useReducedMotion();
  const lamp = size === "lg" ? "size-32" : "size-24";

  return (
    <div
      role="img"
      aria-label={signal ? LAMP_NAME[signal] : "All lamps off: no run yet"}
      className="flex flex-col items-center gap-5 rounded-[2rem] border border-ink-700 bg-ink-900 p-5 shadow-[inset_0_1px_0_rgb(255_255_255/0.05),0_24px_60px_rgb(0_0_0/0.45)]"
    >
      {LAMPS.map((color) => {
        const lit = signal === color;
        const breathe = lit && working && color === "amber" && !reduce;
        return (
          <div key={color} className={`lamp ${lamp}`} data-signal={color}>
            <motion.div
              className="lamp-glow"
              initial={false}
              animate={breathe ? { opacity: [1, 0.62, 1] } : { opacity: lit ? 1 : 0 }}
              transition={
                breathe
                  ? { duration: 1.8, repeat: Infinity, ease: "easeInOut" }
                  : { duration: reduce ? 0 : 0.35, ease: [0.16, 1, 0.3, 1] }
              }
            />
          </div>
        );
      })}
    </div>
  );
}

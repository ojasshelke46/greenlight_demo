"use client";

import { motion } from "motion/react";

type Props = {
  size: number;
  working?: boolean;
  flare?: boolean;
  // Shared layout id lets the hero orb travel into the agent avatar when a run starts.
  layoutId?: string;
  halo?: boolean;
};

export function Orb({ size, working = false, flare = false, layoutId, halo = false }: Props) {
  const orb = (
    <div
      aria-hidden
      className="orb"
      data-working={working}
      data-flare={flare}
      style={{ width: size, height: size }}
    />
  );
  return (
    <motion.div
      layoutId={layoutId}
      // Travel between hero and avatar is on screen movement: strong ease in out, 350ms.
      transition={{ layout: { duration: 0.35, ease: [0.77, 0, 0.175, 1] } }}
      className={halo ? "orb-halo grid place-items-center p-3" : "grid place-items-center"}
    >
      {orb}
    </motion.div>
  );
}

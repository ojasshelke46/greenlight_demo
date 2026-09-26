"use client";

import { motion } from "motion/react";
import { Component as SiriOrb } from "@/components/ui/siri-orb";

// Light, glassy palette: a pale base with translucent pastel lime, mint and cream swirls. No dark
// tones. The lime follows --accent, so the orb tracks the one line accent swap.
const ORB_COLORS = {
  bg: "oklch(98% 0.03 120 / 0.18)",
  c1: "color-mix(in oklch, var(--accent) 70%, transparent)",
  c2: "oklch(93% 0.1 165 / 0.55)",
  c3: "oklch(98% 0.06 95 / 0.6)",
};

type Props = {
  size: number;
  // Faster swirl and a gentle pulse while the agent is executing.
  working?: boolean;
  // One flare when an approval completes.
  flare?: boolean;
  // Shared layout id lets the hero orb travel into the agent avatar when a run starts.
  layoutId?: string;
  // Soft accent bloom behind the hero orb.
  bloom?: boolean;
};

export function Orb({ size, working = false, flare = false, layoutId, bloom = false }: Props) {
  return (
    <motion.div
      layoutId={layoutId}
      // Travel between hero and avatar is on screen movement: strong ease in out, 350ms.
      transition={{ layout: { duration: 0.35, ease: [0.77, 0, 0.175, 1] } }}
      className="orb-shell relative grid place-items-center"
      data-working={working}
      data-flare={flare}
      data-bloom={bloom}
      aria-hidden
    >
      <SiriOrb size={`${size}px`} colors={ORB_COLORS} animationDuration={working ? 6 : 20} />
    </motion.div>
  );
}

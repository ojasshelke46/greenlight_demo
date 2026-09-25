"use client";

import { SidebarSimple } from "@phosphor-icons/react";
import clsx from "clsx";
import { LogoMark } from "@/components/LogoMark";
import { Tip } from "@/components/Tip";

type Props = {
  sidebarOpen: boolean;
  onOpenSidebar: () => void;
  replay: boolean;
  trueforge: boolean | null;
  userName: string;
};

export function TopBar({ sidebarOpen, onOpenSidebar, replay, trueforge, userName }: Props) {
  const initials = userName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  return (
    <header className="relative z-10 flex h-16 min-w-0 shrink-0 items-center gap-3 px-4 sm:px-5">
      {(
        <Tip label="Show sidebar">
          <button type="button" onClick={onOpenSidebar} aria-label="Show sidebar" className={clsx("grid size-9 place-items-center rounded-full text-fg-muted transition-colors duration-150 hover:bg-card hover:text-fg", sidebarOpen && "lg:hidden")}>
            <SidebarSimple weight="bold" className="size-[18px]" aria-hidden />
          </button>
        </Tip>
      )}
      <LogoMark />
      {replay && (
        <span className="ml-2 rounded-full border border-progress/40 bg-progress/10 px-2.5 py-0.5 font-mono text-[0.7rem] font-semibold tracking-[0.14em] text-progress">REPLAY</span>
      )}
      <div className="ml-auto flex items-center gap-4">
        <Tip label={trueforge === null ? "Checking the TrueForge connection" : trueforge ? "The backend can reach TrueForge" : "The backend cannot reach TrueForge"}>
          <span tabIndex={0} className="flex items-center gap-2 text-[0.82rem] text-fg-muted">
            <span className={clsx("size-2 shrink-0 rounded-full", trueforge === null ? "bg-fg-subtle" : trueforge ? "bg-accent shadow-[0_0_8px_var(--accent)]" : "bg-fail")} aria-hidden />
            <span className="max-sm:sr-only">TrueForge</span>
          </span>
        </Tip>
        <span aria-label={`Signed in as ${userName}`} className="grid size-9 place-items-center rounded-full border border-line-strong bg-card font-display text-[0.8rem] font-semibold text-fg">
          {initials || "?"}
        </span>
      </div>
    </header>
  );
}

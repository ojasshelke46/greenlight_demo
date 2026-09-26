import type { Metadata } from "next";
import Link from "next/link";
import { FleetView } from "@/components/features/fleet/FleetView";
import { LogoMark } from "@/components/LogoMark";

export const metadata: Metadata = { title: "Fleet | Greenlight" };

export default function FleetPage() {
  return (
    <div className="relative z-10 min-h-dvh">
      <header className="mx-auto flex h-16 w-full max-w-[1040px] items-center gap-3 px-5">
        <Link href="/" aria-label="Back to Greenlight" className="rounded-xl transition-opacity duration-150 hover:opacity-80">
          <LogoMark />
        </Link>
        <span aria-hidden className="text-fg-subtle">
          /
        </span>
        <span className="font-display text-[1.05rem] font-semibold text-fg">Fleet</span>
      </header>
      <main className="mx-auto w-full max-w-[1040px] px-5 pb-20 pt-8">
        <FleetView />
      </main>
    </div>
  );
}

import Link from "next/link";
import { FleetView } from "@/components/features/fleet/FleetView";

// Shared page file: the fleet feature fills components/features/fleet/FleetView.tsx, not this page.
export default function FleetPage() {
  return (
    <main className="relative z-10 mx-auto flex min-h-dvh w-full max-w-3xl flex-col gap-6 px-5 py-8">
      <Link href="/" className="text-[0.85rem] text-fg-muted transition-colors duration-150 hover:text-fg">
        Back to Greenlight
      </Link>
      <h1 className="font-display text-[1.5rem] font-semibold text-fg">Fleet</h1>
      <FleetView />
    </main>
  );
}

"use client";

import { Tooltip } from "@base-ui/react/tooltip";
import type { ReactElement, ReactNode } from "react";

// Tooltips are frequent and small: 150ms, strong ease out, origin at the trigger.
export function Tip({ label, children }: { label: ReactNode; children: ReactElement }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger render={children} />
      <Tooltip.Portal>
        <Tooltip.Positioner sideOffset={8} className="z-50">
          <Tooltip.Popup className="max-w-72 origin-[var(--transform-origin)] rounded-xl border border-line-strong bg-raised px-3 py-2 text-[0.8rem] leading-snug text-fg shadow-xl transition-[transform,opacity] duration-150 ease-[var(--ease-out)] data-ending-style:scale-[0.97] data-ending-style:opacity-0 data-starting-style:scale-[0.97] data-starting-style:opacity-0">
            {label}
          </Tooltip.Popup>
        </Tooltip.Positioner>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

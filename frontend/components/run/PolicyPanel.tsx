"use client";

import { Hand, Prohibit, RocketLaunch } from "@phosphor-icons/react";
import type { PolicyTier } from "@/lib/state";

const TIER_ICON = {
  auto: { Icon: RocketLaunch, className: "text-fg-muted" },
  approval: { Icon: Hand, className: "text-fg" },
  never: { Icon: Prohibit, className: "text-signal-red" },
} as const;

export function PolicyPanel({ policy }: { policy: PolicyTier[] }) {
  return (
    <aside aria-labelledby="policy-title" className="flex flex-col lg:min-h-0 lg:overflow-y-auto lg:border-l lg:border-ink-700 lg:pl-8">
      <h2 id="policy-title" className="text-2xl font-semibold tracking-tight">
        Policy
      </h2>
      <div className="mt-4 flex flex-col gap-6">
        {policy.map((tier) => {
          const { Icon, className } = TIER_ICON[tier.id];
          const emphasis = tier.id === "approval";
          return (
            <section key={tier.id} aria-label={tier.title}>
              <h3 className={`flex items-center gap-3 font-semibold ${emphasis ? "text-xl" : "text-lg"}`}>
                <Icon weight="bold" className={`size-6 shrink-0 ${className}`} aria-hidden />
                {tier.title}
              </h3>
              {tier.actions.length === 0 ? (
                <p className="mt-2 pl-9 text-base text-fg-muted">Nothing in this run.</p>
              ) : (
                <ul className="mt-2 flex flex-col gap-2 pl-9">
                  {tier.actions.map((action) => (
                    <li
                      key={action.label}
                      className={action.pending ? "rounded-xl border border-fg p-3" : undefined}
                    >
                      <p className={`text-base ${emphasis ? "text-lg font-medium" : ""}`}>{action.label}</p>
                      {action.tool && <p className="font-mono text-[0.85rem] text-fg-subtle">{action.tool}</p>}
                      {action.note && <p className="text-base text-fg-muted">{action.note}</p>}
                      {action.pending && <p className="mt-1 text-base font-semibold">Waiting for you</p>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </aside>
  );
}

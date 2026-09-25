export function LogoMark({ withWordmark = true }: { withWordmark?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <span
        aria-hidden
        className="grid size-8 place-items-center rounded-[10px] bg-accent font-display text-[1.05rem] font-extrabold leading-none text-canvas shadow-[0_0_18px_color-mix(in_oklab,var(--accent)_35%,transparent)]"
      >
        G
      </span>
      {withWordmark && <span className="font-display text-[1.05rem] font-semibold tracking-tight text-fg">Greenlight</span>}
    </span>
  );
}

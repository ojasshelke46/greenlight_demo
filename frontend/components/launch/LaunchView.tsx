"use client";

import {
  ArrowRight,
  GitBranch,
  GitFork,
  Globe,
  Handshake,
  LockSimple,
  Warning,
} from "@phosphor-icons/react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { TrafficLight } from "@/components/TrafficLight";
import type { AccessInfo, AccessState, LaunchState, Mode } from "@/lib/state";

type Props = {
  launch: LaunchState;
  onRepoUrlChange: (url: string) => void;
  onModeChange: (mode: Mode) => void;
  onRun: () => void;
};

export function LaunchView({ launch, onRepoUrlChange, onModeChange, onRun }: Props) {
  const access = launch.access.status === "ready" ? launch.access.access : null;
  const canRun = access !== null && launch.mode !== null;

  return (
    <main className="mx-auto grid min-h-[100dvh] w-full max-w-[1400px] grid-cols-1 gap-12 px-4 pb-6 pt-8 md:px-10 lg:grid-cols-[minmax(0,1fr)_16rem] lg:gap-20">
      <form
        className="flex max-w-3xl flex-col"
        onSubmit={(event) => {
          event.preventDefault();
          if (canRun) onRun();
        }}
      >
        <p className="text-lg font-semibold tracking-tight">Greenlight</p>

        <h1 className="mt-7 text-4xl font-semibold leading-[1.05] tracking-tighter md:text-5xl">
          Which repo should Greenlight fix?
        </h1>
        <p className="mt-3 max-w-[52ch] text-lg leading-relaxed text-fg-muted">
          Greenlight finds vulnerable packages, fixes them in a sandbox until tests pass, and opens a pull request.
        </p>

        <div className="mt-8 flex flex-col gap-2">
          <label htmlFor="repo-url" className="text-base font-medium">
            GitHub repo link
          </label>
          <input
            id="repo-url"
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            autoFocus
            value={launch.repoUrl}
            onChange={(event) => onRepoUrlChange(event.target.value)}
            placeholder="https://github.com/owner/repo"
            aria-describedby="repo-url-help"
            aria-invalid={launch.access.status === "invalid"}
            className="h-18 w-full rounded-xl border border-ink-600 bg-ink-900 px-6 font-mono text-2xl text-fg placeholder:text-fg-subtle transition-colors hover:border-fg-subtle focus:border-fg focus:outline-none"
          />
          <RepoHelp id="repo-url-help" access={launch.access} />
        </div>

        <AccessSection access={launch.access} />

        {access && (
          <ModeSelector access={access} mode={launch.mode} onModeChange={onModeChange} />
        )}

        <div className="mt-7">
          <button
            type="submit"
            disabled={!canRun}
            className="inline-flex h-15 items-center gap-3 rounded-xl bg-fg px-10 text-xl font-semibold text-ink-950 transition-transform duration-150 ease-out hover:bg-fg/90 active:translate-y-px active:scale-[0.99] disabled:cursor-not-allowed disabled:bg-ink-800 disabled:text-fg-subtle disabled:active:scale-100"
          >
            Run
            <ArrowRight weight="bold" className="size-6" aria-hidden />
          </button>
        </div>
      </form>

      <div className="hidden items-start justify-center pt-24 lg:flex">
        <TrafficLight signal={null} size="md" />
      </div>
    </main>
  );
}

function RepoHelp({ id, access }: { id: string; access: AccessState }) {
  if (access.status === "invalid") {
    return (
      <p id={id} className="flex items-center gap-2 text-base text-fg">
        <Warning weight="bold" className="size-5 shrink-0" aria-hidden />
        That is not a repo link. Use the form https://github.com/owner/repo
      </p>
    );
  }
  return (
    <p id={id} className="text-base text-fg-muted">
      Public or private. Greenlight checks its access before anything runs.
    </p>
  );
}

function AccessSection({ access }: { access: AccessState }) {
  const reduce = useReducedMotion();
  return (
    <div className="mt-6" aria-live="polite">
      <AnimatePresence mode="wait" initial={false}>
        {access.status === "checking" && (
          <motion.div
            key="checking"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex flex-col gap-4 rounded-xl border border-ink-700 bg-ink-900 p-6"
            aria-label="Checking access"
          >
            <div className="h-7 w-2/3 animate-pulse rounded-xl bg-ink-800 motion-reduce:animate-none" />
            <div className="h-5 w-1/3 animate-pulse rounded-xl bg-ink-800 motion-reduce:animate-none" />
          </motion.div>
        )}
        {access.status === "error" && (
          <motion.p
            key="error"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900 p-6 text-lg"
          >
            <Warning weight="bold" className="size-6 shrink-0" aria-hidden />
            {access.message}
          </motion.p>
        )}
        {access.status === "ready" && (
          <motion.div
            key={access.access.repo}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
          >
            <AccessCard access={access.access} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function AccessCard({ access }: { access: AccessInfo }) {
  const Icon = access.collaborator ? Handshake : GitFork;
  return (
    <section className="flex items-start gap-4 rounded-xl border border-ink-700 bg-ink-900 p-5" aria-label="Access">
      <Icon weight="bold" className="mt-0.5 size-8 shrink-0" aria-hidden />
      <div className="min-w-0">
        <h2 className="text-2xl font-semibold tracking-tight">
          {access.collaborator ? "Greenlight is a collaborator here" : "Greenlight will fork and open a PR"}
        </h2>
      <dl className="mt-2 flex flex-wrap items-center gap-x-8 gap-y-2 text-base">
        <div className="min-w-0">
          <dt className="sr-only">Repo</dt>
          <dd className="truncate font-mono text-fg-muted">{access.repo}</dd>
        </div>
        <div className="flex items-center gap-2">
          <dt className="sr-only">Visibility</dt>
          {access.private ? (
            <LockSimple weight="bold" className="size-5 text-fg-muted" aria-hidden />
          ) : (
            <Globe weight="bold" className="size-5 text-fg-muted" aria-hidden />
          )}
          <dd>{access.private ? "Private" : "Public"}</dd>
        </div>
        <div className="flex items-center gap-2">
          <dt className="text-fg-muted">
            <GitBranch weight="bold" className="size-5" aria-hidden />
            <span className="sr-only">Default branch</span>
          </dt>
          <dd className="font-mono">{access.defaultBranch}</dd>
        </div>
      </dl>
      </div>
    </section>
  );
}

const MODES: { mode: Mode; title: string; detail: string }[] = [
  { mode: "ship", title: "Ship it", detail: "(merge after your approval)" },
  { mode: "pr_only", title: "PR only", detail: "Opens a pull request and stops there" },
];

function ModeSelector({
  access,
  mode,
  onModeChange,
}: {
  access: AccessInfo;
  mode: Mode | null;
  onModeChange: (mode: Mode) => void;
}) {
  return (
    <fieldset className="mt-6">
      <legend className="text-base font-medium">Mode</legend>
      <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
        {MODES.map((option) => {
          const available = access.modeOptions.includes(option.mode);
          const selected = mode === option.mode;
          return (
            <label
              key={option.mode}
              className={[
                "relative flex gap-4 rounded-xl border p-4 transition-colors",
                available ? "cursor-pointer" : "cursor-not-allowed",
                selected ? "border-fg bg-ink-850" : "border-ink-700 bg-ink-900",
                available && !selected ? "hover:border-fg-subtle" : "",
              ].join(" ")}
            >
              <input
                type="radio"
                name="mode"
                value={option.mode}
                checked={selected}
                disabled={!available}
                onChange={() => onModeChange(option.mode)}
                className="peer sr-only"
              />
              <span
                aria-hidden
                className={[
                  "mt-1 grid size-6 shrink-0 place-items-center rounded-full border-2 peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-fg",
                  selected ? "border-fg" : available ? "border-fg-subtle" : "border-ink-600",
                ].join(" ")}
              >
                {selected && <span className="size-2.5 rounded-full bg-fg" />}
              </span>
              <span className="flex flex-col gap-0.5">
                <span className={`text-xl font-semibold ${available ? "" : "text-fg-subtle"}`}>{option.title}</span>
                <span className={available ? "text-base text-fg-muted" : "text-base text-fg-subtle"}>
                  {option.detail}
                </span>
                {!available && (
                  <span className="mt-1.5 flex items-start gap-2 text-base text-fg-muted">
                    <LockSimple weight="bold" className="mt-0.5 size-5 shrink-0" aria-hidden />
                    Needs push access to this repo.
                  </span>
                )}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

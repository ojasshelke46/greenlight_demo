"use client";

import { Menu } from "@base-ui/react/menu";
import { ArrowsLeftRight, Binoculars, CaretDown, Check, CircleNotch, Cpu, GitFork, Globe, Handshake, ListChecks, LockSimple, Paperclip, Pause, Warning, Wrench } from "@phosphor-icons/react";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { forwardRef, type KeyboardEvent } from "react";
import { Tip } from "@/components/Tip";
import AnimatedGenerateButton from "@/components/ui/animated-generate-button-shadcn-tailwind";
import { CHAT_ROLES, MODE_LABEL, ROLE_LABEL, type AccessState, type AgentInfo, type ChatRole, type Mode } from "@/lib/state";

/** A line above the textarea for agents without an access check: what the input resolved to, or why not. */
export type ComposerNote = { tone: "ok" | "error" | "muted"; text: string };

const AGENT_COPY: Record<ChatRole, { Icon: typeof Wrench; description: string; placeholder: string; hint: string }> = {
  fixer: { Icon: Wrench, description: "Fixes a vulnerable dependency and opens a PR", placeholder: "Paste a GitHub repo link or ask anything", hint: "Paste a GitHub repo link to start a run" },
  scout: { Icon: Binoculars, description: "Ranks many repos by risk. Read only", placeholder: "org:name, user:name, or GitHub repo links", hint: "Try org:name, user:name, or repo links" },
  policy: { Icon: ListChecks, description: "Drafts a .greenlight.yml for a repo", placeholder: "Paste a GitHub repo link to draft its policy", hint: "Paste a GitHub repo link to draft its policy" },
};

type Props = {
  agent: ChatRole;
  onAgentChange: (agent: ChatRole) => void;
  // From GET /api/agents; null while loading.
  agents: AgentInfo[] | null;
  note: ComposerNote | null;
  // Set when the text will go to the open run's agent instead of starting a new run.
  followUpNote: string | null;
  // A run is open: plain text replies to it, a repo link starts a new one.
  hasRun: boolean;
  text: string;
  onTextChange: (text: string) => void;
  hasRepo: boolean;
  access: AccessState;
  mode: Mode;
  onModeChange: (mode: Mode) => void;
  canSend: boolean;
  sending: boolean;
  error: string | null;
  onSend: () => void;
  // While the agent works, the send button becomes the pause button.
  agentWorking: boolean;
  pausing: boolean;
  onPause: () => void;
};

// Chips resolve after the access check: an occasional state change, 200ms strong ease out.
const chipMotion = {
  initial: { opacity: 0, transform: "translateY(4px) scale(0.98)" },
  animate: { opacity: 1, transform: "translateY(0px) scale(1)" },
  exit: { opacity: 0, transform: "translateY(0px) scale(0.98)" },
  transition: { duration: 0.2, ease: [0.23, 1, 0.32, 1] },
} as const;

export const Composer = forwardRef<HTMLTextAreaElement, Props>(function Composer(
  { agent, onAgentChange, agents, note, followUpNote, hasRun, text, onTextChange, hasRepo, access, mode, onModeChange, canSend, sending, error, onSend, agentWorking, pausing, onPause },
  ref,
) {
  const reduce = useReducedMotion();
  const chip = reduce ? { ...chipMotion, initial: { opacity: 0 }, exit: { opacity: 0 } } : chipMotion;
  const ready = access.status === "ready" ? access.access : null;
  const shipAllowed = ready ? ready.modeOptions.includes("ship") : true;
  const fix = agent === "fixer";
  const copy = AGENT_COPY[agent];

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (canSend && !agentWorking && !pausing) onSend();
    }
  };

  return (
    <div className="composer rounded-[var(--radius-composer)] border border-line bg-[#090909]/95 p-3 shadow-[0_24px_60px_-20px_rgb(0_0_0/0.8)] backdrop-blur-sm">
      <div className="flex flex-wrap items-center gap-2 px-2 pt-1 empty:hidden" aria-live="polite">
        <AnimatePresence mode="popLayout" initial={false}>
          {fix && access.status === "checking" && (
            <motion.span key="checking" {...chip} className="inline-flex h-8 items-center gap-2 rounded-full border border-line bg-card px-3 text-[0.82rem] text-fg-muted">
              <CircleNotch weight="bold" className="size-3.5 animate-spin text-accent motion-reduce:animate-none" aria-hidden />
              Checking access to <span className="font-mono text-fg">{access.repo}</span>
            </motion.span>
          )}
          {fix && ready && (
            <motion.span key={`access:${ready.repo}`} {...chip} className="inline-flex h-8 items-center gap-2 rounded-full border border-line bg-card px-3 text-[0.82rem] text-fg">
              {ready.collaborator ? <Handshake weight="bold" className="size-4 text-accent" aria-hidden /> : <GitFork weight="bold" className="size-4 text-fg-muted" aria-hidden />}
              {ready.collaborator ? "Collaborator: can ship after your approval" : "Not a collaborator: will fork and open a PR"}
            </motion.span>
          )}
          {fix && ready && (
            <motion.span key={`vis:${ready.repo}`} {...chip} className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line px-3 text-[0.82rem] text-fg-muted">
              {ready.private ? <LockSimple weight="bold" className="size-3.5" aria-hidden /> : <Globe weight="bold" className="size-3.5" aria-hidden />}
              {ready.private ? "Private" : "Public"}
            </motion.span>
          )}
          {fix && access.status === "error" && (
            <motion.span key="error" {...chip} className="inline-flex h-8 items-center gap-2 rounded-full border border-fail/30 bg-fail/10 px-3 text-[0.82rem] text-fail">
              <Warning weight="bold" className="size-4" aria-hidden />
              {access.message}
            </motion.span>
          )}
          {followUpNote && (
            <motion.span key={`followup:${followUpNote}`} {...chip} className="inline-flex h-8 items-center px-1 text-[0.82rem] text-fg-subtle">
              {followUpNote}
            </motion.span>
          )}
          {!followUpNote && fix && access.status === "idle" && text.trim() !== "" && !hasRepo && (
            <motion.span key="hint" {...chip} className="inline-flex h-8 items-center px-1 text-[0.82rem] text-fg-subtle">
              {copy.hint}
            </motion.span>
          )}
          {!followUpNote && !fix && note && (
            <motion.span
              key={`note:${note.text}`}
              {...chip}
              className={clsx(
                "inline-flex h-8 items-center gap-2 rounded-full border px-3 text-[0.82rem]",
                note.tone === "error" ? "border-fail/30 bg-fail/10 text-fail" : note.tone === "ok" ? "border-line bg-card text-fg" : "border-transparent px-1 text-fg-subtle",
              )}
            >
              {note.tone === "error" && <Warning weight="bold" className="size-4" aria-hidden />}
              {note.text}
            </motion.span>
          )}
        </AnimatePresence>
      </div>

      <label htmlFor="composer-input" className="sr-only">
        Repo link or message
      </label>
      <textarea
        id="composer-input"
        ref={ref}
        rows={1}
        value={text}
        onChange={(event) => onTextChange(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={hasRun ? "Reply to the agent, or paste a repo link to start a new run" : copy.placeholder}
        spellCheck={false}
        className="block max-h-48 min-h-14 w-full resize-none bg-transparent px-3 py-3 text-[1.05rem] leading-relaxed text-fg outline-none focus-visible:outline-none [field-sizing:content] placeholder:text-fg-subtle"
      />

      <div className="flex min-w-0 items-center gap-2 px-1 pb-0.5">
        <Tip label="Attachments are not supported yet">
          <button
            type="button"
            aria-disabled
            aria-label="Attach a file (not supported yet)"
            className="grid size-9 cursor-not-allowed place-items-center rounded-full border border-line-strong text-fg-subtle"
          >
            <Paperclip weight="bold" className="size-4" aria-hidden />
          </button>
        </Tip>

        <AgentChip agent={agent} onAgentChange={onAgentChange} agents={agents} />
        <ModelChip info={agents?.find((a) => a.role === agent) ?? null} loading={agents === null} />
        {fix && <ModeChip mode={mode} onModeChange={onModeChange} shipAllowed={shipAllowed} repo={ready?.repo ?? null} />}

        <div className="ml-auto flex min-w-0 items-center gap-3">
          {error && (
            <p role="alert" className="max-w-80 truncate text-[0.82rem] text-fail" title={error}>
              {error}
            </p>
          )}
          {agentWorking || pausing ? (
            <Tip label={pausing ? "Waiting for the agent to stop" : "Pause the agent"}>
              <button
                type="button"
                onClick={() => !pausing && onPause()}
                aria-disabled={pausing}
                aria-label={pausing ? "Pausing the agent" : "Pause the agent"}
                className={clsx(
                  "grid size-11 place-items-center rounded-full border transition-[transform,border-color,background-color] duration-150 ease-out active:scale-[0.96]",
                  pausing ? "cursor-wait border-line-strong bg-raised text-fg-muted" : "border-progress/50 bg-progress/10 text-progress hover:bg-progress/20",
                )}
              >
                {pausing ? <CircleNotch weight="bold" className="size-5 animate-spin motion-reduce:animate-none" aria-hidden /> : <Pause weight="fill" className="size-5" aria-hidden />}
              </button>
            </Tip>
          ) : (
            <AnimatedGenerateButton
              labelIdle="Send"
              labelActive="Starting"
              generating={sending}
              disabled={!canSend}
              onClick={onSend}
              ariaLabel={sending ? "Starting the run" : "Start the run"}
            />
          )}
        </div>
      </div>
    </div>
  );
});

function ModeChip({ mode, onModeChange, shipAllowed, repo }: { mode: Mode; onModeChange: (mode: Mode) => void; shipAllowed: boolean; repo: string | null }) {
  const next: Mode = mode === "ship" ? "pr_only" : "ship";
  const blocked = next === "ship" && !shipAllowed;
  const chip = (
    <button
      type="button"
      aria-disabled={blocked}
      onClick={() => !blocked && onModeChange(next)}
      aria-label={`Mode: ${MODE_LABEL[mode]}. ${blocked ? "Ship it needs push access." : `Switch to ${MODE_LABEL[next]}.`}`}
      className={clsx(
        "inline-flex h-9 items-center gap-2 rounded-full border px-3 text-[0.82rem] font-medium transition-colors duration-150",
        mode === "ship" ? "border-accent/40 bg-accent/10 text-accent" : "border-line bg-card text-fg",
        blocked ? "cursor-not-allowed" : "hover:border-line-strong",
      )}
    >
      <ArrowsLeftRight weight="bold" className="size-4" aria-hidden />
      {MODE_LABEL[mode]}
    </button>
  );
  if (blocked) {
    return <Tip label={`Ship it needs push access. The bot is not a collaborator on ${repo}, so this run can only open a PR.`}>{chip}</Tip>;
  }
  return <Tip label={mode === "ship" ? "Merges after your approval. Click for PR only." : "Opens a pull request and stops. Click for Ship it."}>{chip}</Tip>;
}

// The agent menu opens a few times a session: 150ms from its trigger, strong ease out, like the run menu.
function AgentChip({ agent, onAgentChange, agents }: { agent: ChatRole; onAgentChange: (agent: ChatRole) => void; agents: AgentInfo[] | null }) {
  const { Icon } = AGENT_COPY[agent];
  const startable = CHAT_ROLES.filter((role) => agents?.find((a) => a.role === role)?.chat ?? true);
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={`Agent: ${ROLE_LABEL[agent]}. Change agent`}
        className="inline-flex h-9 shrink-0 items-center gap-2 rounded-full border border-line bg-card px-2.5 text-[0.82rem] font-medium text-fg transition-colors duration-150 hover:border-line-strong data-popup-open:border-line-strong sm:px-3"
      >
        <Icon weight="bold" className="size-4 text-accent" aria-hidden />
        {ROLE_LABEL[agent]}
        <CaretDown weight="bold" className="size-3.5 text-fg-subtle" aria-hidden />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side="top" sideOffset={8} align="start" className="z-50 outline-none">
          <Menu.Popup className="min-w-64 origin-[var(--transform-origin)] rounded-xl border border-line-strong bg-raised p-1 shadow-2xl outline-none transition-[transform,opacity] duration-150 ease-[var(--ease-out)] data-ending-style:scale-[0.97] data-ending-style:opacity-0 data-starting-style:scale-[0.97] data-starting-style:opacity-0 motion-reduce:data-ending-style:scale-100 motion-reduce:data-starting-style:scale-100">
            {startable.map((role) => {
              const { Icon: RoleIcon, description } = AGENT_COPY[role];
              return (
                <Menu.Item key={role} onClick={() => onAgentChange(role)} className="flex cursor-default items-start gap-3 rounded-lg px-3 py-2 outline-none select-none data-highlighted:bg-card">
                  <RoleIcon weight="bold" className="mt-0.5 size-4 shrink-0 text-fg-muted" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[0.85rem] font-medium text-fg">{ROLE_LABEL[role]}</span>
                    <span className="block text-[0.78rem] text-fg-subtle">{description}</span>
                  </span>
                  {role === agent && <Check weight="bold" className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />}
                </Menu.Item>
              );
            })}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}

/** The selected agent's real model, as TrueForge has it configured. */
function ModelChip({ info, loading }: { info: AgentInfo | null; loading: boolean }) {
  const missing = !loading && (!info || !info.found);
  const label = loading ? "Loading model" : missing ? "Agent not found" : (info?.model ?? "Model not set");
  return (
    <Tip label={missing ? `TrueForge has no agent named ${info?.name ?? "for this role"}` : `The ${info?.name ?? ""} agent runs on TrueForge with this model`}>
      <span
        tabIndex={0}
        aria-label={`Model: ${label}`}
        className={clsx(
          "inline-flex h-9 min-w-0 shrink items-center gap-2 rounded-full border border-line bg-card px-2.5 text-[0.82rem] sm:px-3",
          missing ? "text-fail" : "text-fg-muted",
        )}
      >
        {missing ? <Warning weight="bold" className="size-4 shrink-0" aria-hidden /> : <Cpu weight="bold" className="size-4 shrink-0 text-accent" aria-hidden />}
        <span className="hidden truncate font-mono text-[0.78rem] sm:inline">{label}</span>
      </span>
    </Tip>
  );
}

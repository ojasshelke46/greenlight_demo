"use client";

import type { ReactNode } from "react";
import { CopyButton } from "./Blocks";
import { splitThoughts, ThoughtBlock } from "./Thought";

/** An agent's text without its <think> sections, e.g. a helper's final report. */
export function withoutThoughts(text: string): string {
  return splitThoughts(text)
    .filter((segment) => segment.kind === "text")
    .map((segment) => segment.text)
    .join("\n\n");
}

/** Prose and <think> sections in order. Only a thought still streaming at the tail of a live run animates. */
export function AgentMessage({ text, live }: { text: string; live: boolean }) {
  const segments = splitThoughts(text);
  return (
    <div className="flex flex-col gap-3">
      {segments.map((segment, i) =>
        segment.kind === "thought" ? (
          <ThoughtBlock key={i} text={segment.text} live={live && segment.open && i === segments.length - 1} />
        ) : (
          <AgentText key={i} text={segment.text} />
        ),
      )}
    </div>
  );
}

const FENCE = /```([\w+-]*)[ \t]*\n([\s\S]*?)(?:```|$)/g;

/** The agent's own words. Light formatting only: fenced code, paragraphs, bullets, bold and inline code. */
export function AgentText({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(FENCE)) {
    const before = text.slice(last, match.index);
    if (before.trim()) parts.push(<Prose key={`p${last}`} text={before.trim()} />);
    parts.push(<CodeBlock key={`c${match.index}`} lang={match[1]} code={match[2].replace(/\n$/, "")} />);
    last = match.index + match[0].length;
  }
  const rest = text.slice(last);
  if (rest.trim()) parts.push(<Prose key={`p${last}`} text={rest.trim()} />);
  return <div className="flex flex-col gap-2.5">{parts}</div>;
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  return (
    <div className="overflow-hidden rounded-[var(--radius-card)] border border-line bg-[#070707]">
      <div className="flex items-center gap-2 border-b border-line py-1 pl-4 pr-1.5">
        <span className="font-mono text-[0.75rem] text-fg-subtle">{lang || "text"}</span>
        <span className="ml-auto">
          <CopyButton text={code} label="Copy code" />
        </span>
      </div>
      <pre className="max-h-96 overflow-auto px-4 py-3 font-mono text-[0.8rem] leading-relaxed text-fg">{code}</pre>
    </div>
  );
}

function Prose({ text }: { text: string }) {
  const paragraphs = text.split(/\n{2,}/);
  return (
    <div className="flex flex-col gap-2.5 text-[0.95rem] leading-relaxed text-fg">
      {paragraphs.map((para, i) => {
        const lines = para.split("\n");
        if (lines.length >= 2 && lines.every((l) => /^\s*\|.*\|\s*$/.test(l))) return <Table key={i} lines={lines} />;
        if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
          return (
            <ul key={i} className="flex list-disc flex-col gap-1 pl-5 marker:text-fg-subtle">
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>
              ))}
            </ul>
          );
        }
        if (/^#{1,4}\s/.test(para)) {
          return (
            <p key={i} className="font-display font-semibold text-fg">
              {inline(para.replace(/^#{1,4}\s/, ""))}
            </p>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap text-fg/90">
            {inline(para)}
          </p>
        );
      })}
    </div>
  );
}

/** A markdown table the agent wrote: header row, separator, body rows. */
function Table({ lines }: { lines: string[] }) {
  const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const rows = lines.filter((l) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(l)).map(cells);
  const [head, ...body] = rows;
  return (
    <div className="overflow-x-auto rounded-[var(--radius-card)] border border-line">
      <table className="w-full text-left text-[0.85rem]">
        <thead className="bg-card">
          <tr>
            {head.map((cell, j) => (
              <th key={j} className="px-3 py-2 font-medium text-fg-muted">
                {inline(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, r) => (
            <tr key={r} className="border-t border-line">
              {row.map((cell, j) => (
                <td key={j} className="px-3 py-2 align-top text-fg/90">
                  {inline(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function inline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="rounded-md bg-card px-1.5 py-0.5 font-mono text-[0.85em] text-fg">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-semibold text-fg">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return part;
  });
}

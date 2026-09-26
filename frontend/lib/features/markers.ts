// The one parser for facts the agent reports as lines of the form: GREENLIGHT_FACT {"kind": "...", ...}
// Same rules as backend/app/markers.py; keep the two in step.

export const MARKER = "GREENLIGHT_FACT ";

export type Fact = { kind: string; [key: string]: unknown };

/** Every well formed fact in text, in order. Malformed lines are skipped, never thrown. */
export function extractFacts(text: string | null | undefined): Fact[] {
  if (!text || !text.includes(MARKER)) return [];
  const facts: Fact[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line.startsWith(MARKER)) continue;
    let value: unknown;
    try {
      value = JSON.parse(line.slice(MARKER.length));
    } catch {
      continue;
    }
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      const kind = (value as { kind?: unknown }).kind;
      if (typeof kind === "string" && kind !== "") facts.push(value as Fact);
    }
  }
  return facts;
}

/** Text of a model.message content field: a string, a list of content parts, or null. */
export function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((part) => (part && typeof part === "object" && typeof part.text === "string" ? part.text : "")).join("");
  }
  return "";
}

/**
 * Facts from the live event stream, each returned once, as soon as its line is complete.
 * An agent message arrives either as one complete model.message, or as an empty model.message
 * followed by model.message.delta fragments, so a fact line can be split across events.
 */
export class FactStream {
  private messages = new Map<string, { text: string; done: number }>();
  private open = new Set<string>();

  feed(event: Record<string, unknown>): Fact[] {
    const type = event.type;
    const id = event.id;
    if ((type !== "model.message" && type !== "model.message.delta") || typeof id !== "string") return this.flushOpen();

    const found = this.flushOpen(id);
    let buffer = this.messages.get(id);
    if (!buffer) {
      buffer = { text: "", done: 0 };
      this.messages.set(id, buffer);
    }
    if (type === "model.message") {
      const text = messageText(event.content);
      if (!text) {
        this.open.add(id);
        return found;
      }
      buffer.text = text;
      return found.concat(this.drain(id, true));
    }
    if (typeof event.content === "string") buffer.text += event.content;
    this.open.add(id);
    return found.concat(this.drain(id, Boolean(event.finish_reason)));
  }

  private flushOpen(keep?: string): Fact[] {
    const found: Fact[] = [];
    for (const id of [...this.open]) if (id !== keep) found.push(...this.drain(id, true));
    return found;
  }

  private drain(id: string, final: boolean): Fact[] {
    const buffer = this.messages.get(id)!;
    const lines = buffer.text.split("\n");
    const upto = final ? lines.length : lines.length - 1;
    const found = lines.slice(buffer.done, upto).flatMap((line) => extractFacts(line));
    buffer.done = Math.max(buffer.done, upto);
    if (final) this.open.delete(id);
    return found;
  }
}

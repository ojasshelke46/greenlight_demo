// Projects the raw TrueForge event stream (relayed by the backend SSE) onto RunState.
// Every block and step here comes from a real event: the agent's own messages, the tool calls it
// made, their outputs, and the turn lifecycle. Nothing is shown before the event that proves it.

import type {
  AgentQuestion,
  ApprovalContext,
  ApprovalState,
  Block,
  DiffFile,
  Failure,
  PendingApproval,
  PullRequest,
  Release,
  RunMeta,
  RunState,
  RunStatus,
  Step,
  StepId,
  StepStatus,
  TerminalCommand,
  TerminalLine,
  Tone,
  Vulnerability,
} from "./state";

type Json = Record<string, unknown>;

type RawToolCall = {
  id?: string;
  function: { name: string; arguments: string };
  tool_info?: { type?: string; name?: string; server_name?: string } | null;
};

type Message = { id: string; seq: number; at: number; content: string; toolCalls: RawToolCall[] };

type ToolResponse = { content: string; seq: number; at: number };

type Resolved = { server: string | null; name: string; input: Json | null; complete: boolean };

type TurnEnd = { status: string; reason?: string; message?: string; seq: number };

const MAX_LINES_PER_COMMAND = 80;
const QUIET_TOOLS = new Set(["list_tools", "get_tool_info"]);

const TEST_CMD = /\b(npm (run )?test|npm t\b|node --test|pnpm test|yarn test|npx (jest|vitest|mocha))/;
const AUDIT_CMD = /\bnpm audit\b(?! fix)|osv\.dev/;
const UPGRADE_CMD = /\bnpm (install|i|add|update|up)\b[^&|;]*\S@\S|\bnpm (update|up)\b|\bnpm audit fix\b/;

function asObject(value: unknown): Json | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Json) : null;
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function timeOf(event: Json, fallback: number): number {
  const at = str(event.created_at);
  const parsed = at ? Date.parse(at) : NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function resolveToolCall(call: RawToolCall): Resolved {
  const info = call.tool_info ?? {};
  const parsed = parseJson(call.function.arguments);
  const args = asObject(parsed);
  const complete = parsed !== undefined;

  if (info.type === "truefoundry-system" && info.name === "call_tool") {
    return { server: str(args?.mcp_server), name: str(args?.tool_name) ?? "call_tool", input: asObject(args?.input), complete };
  }
  if (info.type === "mcp") {
    return { server: info.server_name ?? null, name: info.name ?? call.function.name, input: args, complete };
  }
  return { server: null, name: call.function.name || info.name || "tool", input: args, complete };
}

type ExecResult = { ok: boolean; exitCode: number | null; output: string };

function execResult(content: string): ExecResult {
  const body = asObject(parseJson(content));
  if (body && Array.isArray(body.error)) {
    return { ok: false, exitCode: null, output: body.error.map((part) => str(asObject(part)?.text) ?? "").join("\n") };
  }
  const response = asObject(body?.response);
  if (body && response) {
    const exitCode = typeof response.exitCode === "number" ? response.exitCode : null;
    return { ok: body.success !== false && (exitCode === null || exitCode === 0), exitCode, output: str(response.result) ?? "" };
  }
  return { ok: true, exitCode: null, output: content };
}

function toolFailed(content: string): string | null {
  const body = asObject(parseJson(content));
  if (body && Array.isArray(body.error)) {
    return body.error.map((part) => str(asObject(part)?.text) ?? "").join(" ").trim() || "Tool call failed";
  }
  return null;
}

type TestCounts = { pass: number | null; fail: number | null };

function testCounts(output: string): TestCounts {
  const pass = /(?:ℹ|#)\s*pass\s+(\d+)/.exec(output) ?? /Tests:.*?(\d+) passed/.exec(output);
  const fail = /(?:ℹ|#)\s*fail\s+(\d+)/.exec(output) ?? /Tests:.*?(\d+) failed/.exec(output);
  return { pass: pass ? Number(pass[1]) : null, fail: fail ? Number(fail[1]) : pass ? 0 : null };
}

function lineKind(line: string): TerminalLine["kind"] {
  if (/^\s*(✔|ok \d|✓|PASS\b)/.test(line)) return "pass";
  if (/^\s*(✖|not ok|✗|FAIL\b|npm ERR!|Error\b|\w*Error:)/.test(line)) return "fail";
  return "output";
}

function parseAudit(output: string): Vulnerability[] | null {
  const start = output.indexOf("{");
  const report = start >= 0 ? asObject(parseJson(output.slice(start))) : null;
  const vulns = asObject(report?.vulnerabilities);
  if (vulns) {
    return Object.values(vulns).flatMap((entry) => {
      const v = asObject(entry);
      if (!v) return [];
      const via = Array.isArray(v.via) ? v.via.map(asObject).find((item) => item && str(item.url)) : null;
      const url = str(via?.url) ?? undefined;
      const fix = asObject(v.fixAvailable);
      return [
        {
          advisory: url?.match(/GHSA-[a-z0-9-]+/i)?.[0] ?? str(via?.source) ?? "Advisory",
          packageName: str(v.name) ?? "unknown",
          affected: str(v.range) ?? "",
          patched: str(fix?.version) ?? (v.fixAvailable ? "fix available" : "no fix listed"),
          severity: str(v.severity) ?? "",
          summary: str(via?.title) ?? "",
          url,
          status: "open" as const,
        },
      ];
    });
  }
  const ids = Array.from(new Set(output.match(/GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}/gi) ?? []));
  if (ids.length === 0) return /found 0 vulnerabilities/.test(output) ? [] : null;
  return ids.map((advisory) => ({
    advisory,
    packageName: /^([@\w./-]+)\s+[<>=^~*\d]/m.exec(output)?.[1] ?? "unknown",
    affected: "",
    patched: "",
    severity: /Severity:\s*(\w+)/i.exec(output)?.[1] ?? "",
    summary: "",
    url: `https://github.com/advisories/${advisory}`,
    status: "open" as const,
  }));
}

function parseDiff(output: string): DiffFile[] | null {
  if (!output.includes("diff --git")) return null;
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let hunk: DiffFile["hunks"][number] | null = null;
  for (const line of output.split("\n")) {
    const header = /^diff --git a\/(.+?) b\/(.+)$/.exec(line);
    if (header) {
      file = { path: header[2], hunks: [] };
      files.push(file);
      hunk = null;
    } else if (!file || /^(index |--- |\+\+\+ |new file|deleted file|similarity|rename )/.test(line)) {
      continue;
    } else if (line.startsWith("@@")) {
      hunk = { header: line.replace(/^(@@[^@]*@@).*$/, "$1"), lines: [] };
      file.hunks.push(hunk);
    } else if (hunk) {
      hunk.lines.push({ kind: line.startsWith("+") ? "add" : line.startsWith("-") ? "remove" : "context", text: line.slice(1) });
    }
  }
  return files;
}

function packageSpec(command: string): string | null {
  const match = /\bnpm (?:install|i|add|update|up)\b\s+((?:[@\w./-]+@[\w.^~*-]+\s*)+)/.exec(command);
  return match ? match[1].trim() : null;
}

function prFromResponse(content: string): { number: number | null; url: string | null; title: string | null } {
  const body = asObject(parseJson(content));
  const url = str(body?.html_url) ?? /https:\/\/github\.com\/[^\s"]+\/pull\/\d+/.exec(content)?.[0] ?? null;
  const number = typeof body?.number === "number" ? body.number : url ? Number(url.split("/").pop()) : null;
  return { number, url, title: str(body?.title) };
}

function shorten(text: string, max = 160): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

const TOOL_ACTIVITY: Record<string, string> = {
  create_branch: "Creating the greenlight branch",
  push_files: "Pushing the fix",
  create_or_update_file: "Committing a file",
  create_pull_request: "Opening the pull request",
  fork_repository: "Forking the repo",
  merge_pull_request: "Merging the pull request",
  get_file_contents: "Reading the repo",
  list_branches: "Reading branches",
};

type StepFact = { status: StepStatus; detail?: string; tone?: Tone; startedAt?: number; endedAt?: number };

export class RunModel {
  readonly meta: RunMeta;
  eventCount = 0;
  lastSequence = 0;

  private messages = new Map<string, Message>();
  private order: string[] = [];
  private responses = new Map<string, ToolResponse>();
  private sandboxAt: number | null = null;
  private startedAt: number | null = null;
  private turnsStarted = 0;
  private pendingRefs: string[] | null = null;
  private questionRefs: string[] | null = null;
  private turnEnd: TurnEnd | null = null;
  // Sequence of the latest turn.done seen, kept across resumes to spot calls a past turn abandoned.
  private lastTurnDoneSeq = 0;

  constructor(meta: RunMeta) {
    this.meta = meta;
  }

  apply(sequence: number, event: Json): void {
    this.eventCount += 1;
    this.lastSequence = Math.max(this.lastSequence, sequence);
    const type = str(event.type);
    const id = str(event.id) ?? `seq${sequence}`;
    const at = timeOf(event, Date.now());

    switch (type) {
      case "turn.created":
        this.turnsStarted += 1;
        this.startedAt ??= at;
        this.turnEnd = null;
        this.pendingRefs = null;
        this.questionRefs = null;
        break;
      case "turn.done": {
        const state = asObject(event.state) ?? {};
        this.turnEnd = { status: str(state.status) ?? "done", reason: str(state.reason) ?? undefined, message: str(state.message) ?? undefined, seq: sequence };
        this.lastTurnDoneSeq = sequence;
        break;
      }
      case "sandbox.created":
        this.sandboxAt ??= at;
        break;
      case "model.message": {
        const message = this.message(id, sequence, at);
        const content = str(event.content);
        if (content) message.content = content;
        if (Array.isArray(event.tool_calls) && event.tool_calls.length > 0) {
          message.toolCalls = event.tool_calls.map((raw) => {
            const call = asObject(raw) ?? {};
            const fn = asObject(call.function) ?? {};
            return {
              id: str(call.id) ?? undefined,
              function: { name: str(fn.name) ?? "", arguments: str(fn.arguments) ?? "" },
              tool_info: asObject(call.tool_info) as RawToolCall["tool_info"],
            };
          });
        }
        break;
      }
      case "model.message.delta": {
        const message = this.message(id, sequence, at);
        const content = str(event.content);
        if (content) message.content += content;
        if (Array.isArray(event.tool_calls)) {
          for (const raw of event.tool_calls) this.mergeToolCall(message, asObject(raw) ?? {});
        }
        break;
      }
      case "tool.response": {
        const callId = str(event.tool_call_id);
        const content = str(event.content) ?? JSON.stringify(event.content ?? "");
        if (callId) this.responses.set(callId, { content, seq: sequence, at });
        break;
      }
      case "tool.response_required": {
        const refs = Array.isArray(event.tool_calls) ? event.tool_calls.map((r) => str(asObject(r)?.id)).filter(Boolean) : [];
        this.questionRefs = refs as string[];
        break;
      }
      case "tool.approval_required": {
        const refs = Array.isArray(event.tool_calls) ? event.tool_calls.map((r) => str(asObject(r)?.id)).filter(Boolean) : [];
        this.pendingRefs = refs as string[];
        break;
      }
    }
  }

  private message(id: string, seq: number, at: number): Message {
    let message = this.messages.get(id);
    if (!message) {
      message = { id, seq, at, content: "", toolCalls: [] };
      this.messages.set(id, message);
      this.order.push(id);
    }
    return message;
  }

  private mergeToolCall(message: Message, delta: Json): void {
    const index = typeof delta.index === "number" ? delta.index : message.toolCalls.length;
    const fn = asObject(delta.function) ?? {};
    const existing = message.toolCalls[index];
    if (existing) {
      if (str(delta.id)) existing.id = str(delta.id)!;
      if (asObject(delta.tool_info)) existing.tool_info = asObject(delta.tool_info) as RawToolCall["tool_info"];
      if (str(fn.name)) existing.function.name = str(fn.name)!;
      if (str(fn.arguments)) existing.function.arguments += str(fn.arguments)!;
    } else if (index === message.toolCalls.length) {
      message.toolCalls.push({
        id: str(delta.id) ?? undefined,
        function: { name: str(fn.name) ?? "", arguments: str(fn.arguments) ?? "" },
        tool_info: asObject(delta.tool_info) as RawToolCall["tool_info"],
      });
    }
  }

  get paused(): boolean {
    return (this.pendingRefs !== null || this.questionRefs !== null) && this.turnEnd?.status === "done";
  }

  question(): AgentQuestion | null {
    const ref = this.questionRefs?.[0];
    if (!ref || this.turnEnd?.status !== "done") return null;
    for (const id of this.order) {
      const call = this.messages.get(id)!.toolCalls.find((c) => c.id === ref);
      if (!call) continue;
      const input = resolveToolCall(call).input ?? {};
      const options = Array.isArray(input.options) ? input.options.filter((o): o is string => typeof o === "string") : [];
      return { text: str(input.question) ?? "The agent is waiting for an answer.", options };
    }
    return { text: "The agent is waiting for an answer.", options: [] };
  }

  get turnFinished(): boolean {
    return this.turnEnd !== null;
  }

  pendingApproval(): PendingApproval | null {
    const ref = this.pendingRefs?.[0];
    if (!ref) return null;
    for (const id of this.order) {
      const call = this.messages.get(id)!.toolCalls.find((c) => c.id === ref);
      if (!call) continue;
      const resolved = resolveToolCall(call);
      const pullNumber = typeof resolved.input?.pullNumber === "number" ? resolved.input.pullNumber : null;
      return { tool: resolved.name, server: resolved.server, arguments: resolved.input, pullNumber, pullUrl: null };
    }
    return null;
  }

  get userPaused(): boolean {
    return this.turnEnd?.status === "cancelled" && this.turnEnd.reason === "client-cancelled";
  }

  snapshot(approval: ApprovalState, release: Release | null, connection: RunState["connection"], pausing = false): RunState {
    const blocks: Block[] = [];
    let terminal: Extract<Block, { kind: "terminal" }> | null = null;
    const facts: Partial<Record<StepId, StepFact>> = {};
    const set = (step: StepId, fact: StepFact) => {
      facts[step] = { ...fact, startedAt: facts[step]?.startedAt ?? fact.startedAt };
    };

    let vulnerabilities: Vulnerability[] | null = null;
    let vulnBlock: Extract<Block, { kind: "vulns" }> | null = null;
    let diffBlock: Extract<Block, { kind: "diff" }> | null = null;
    let upgraded = false;
    const postTests: { ok: boolean; counts: TestCounts; running: boolean; at: number; endAt?: number }[] = [];
    let pr: PullRequest | null = null;
    let prPending: { title: string | null; branch: string | null; base: string | null } | null = null;
    let merged: boolean | null = null;
    let current: string | null = null;
    let lastText: string | null = null;
    let anyExec = false;
    let pushedFiles: string[] = [];

    for (const messageId of this.order) {
      const message = this.messages.get(messageId)!;
      const text = message.content.trim();
      if (text) {
        blocks.push({ kind: "text", id: `${messageId}:text`, text });
        terminal = null;
        lastText = text;
        current = shorten(text.split("\n").filter(Boolean).pop() ?? text, 90);
      }

      for (const call of message.toolCalls) {
        const resolved = resolveToolCall(call);
        const response = call.id ? this.responses.get(call.id) : undefined;
        const key = call.id ?? `${messageId}:${resolved.name}`;
        const started = message.at;
        const ended = response?.at;
        const stopped = !response && message.seq < this.lastTurnDoneSeq;
        if (!resolved.complete && !response) {
          if (resolved.name === "exec") current = "Preparing a sandbox command";
          continue;
        }

        if (resolved.name === "exec" && resolved.server === null) {
          anyExec = true;
          const command = str(resolved.input?.command) ?? "";
          const intent = str(resolved.input?.intent);
          const result = response ? execResult(response.content) : null;
          if (!response && !stopped) current = intent ?? shorten(command, 90);

          const lines: TerminalLine[] = [];
          if (result) {
            const all = result.output.replace(/\s+$/, "").split("\n").filter((l, i, arr) => l !== "" || i < arr.length - 1);
            const tail = all.slice(-MAX_LINES_PER_COMMAND);
            if (all.length > tail.length) lines.push({ id: `${key}:skip`, kind: "output", text: `… ${all.length - tail.length} earlier lines` });
            tail.forEach((line, i) => lines.push({ id: `${key}:${i}`, kind: lineKind(line), text: line }));
          }
          const entry: TerminalCommand = { id: key, command, intent, running: !result && !stopped, stopped, ok: result ? result.ok : null, exitCode: result?.exitCode ?? null, lines };
          if (!terminal) {
            terminal = { kind: "terminal", id: `${key}:terminal`, commands: [] };
            blocks.push(terminal);
          }
          terminal.commands.push(entry);

          if (AUDIT_CMD.test(command)) {
            set("scan", result ? { status: "done", startedAt: started, endedAt: ended } : { status: "active", detail: "Running npm audit", tone: "progress", startedAt: started });
            const found = result ? parseAudit(result.output) : null;
            if (found !== null) {
              if (vulnerabilities === null) {
                vulnerabilities = found;
                if (found.length > 0) {
                  vulnBlock = { kind: "vulns", id: `${key}:vulns`, items: found };
                  blocks.push(vulnBlock);
                  terminal = null;
                }
              } else {
                const still = new Set(found.map((v) => v.packageName));
                vulnerabilities = vulnerabilities.map((v) => (still.has(v.packageName) ? v : { ...v, status: "fixed" }));
              }
              const count = vulnerabilities.filter((v) => v.status !== "fixed").length;
              if (facts.scan?.status === "done" && !facts.scan.detail) {
                facts.scan.detail = count > 0 ? `${count} vulnerable ${count === 1 ? "package" : "packages"}` : "No vulnerable packages";
                facts.scan.tone = count > 0 ? "fail" : "done";
              }
            }
          }
          if (UPGRADE_CMD.test(command)) {
            upgraded = true;
            const spec = packageSpec(command);
            set("upgrade", !result
              ? { status: "active", detail: spec ?? "Upgrading", tone: "progress", startedAt: started }
              : result.ok
                ? { status: "done", detail: spec ?? "Upgraded", startedAt: started, endedAt: ended }
                : { status: "failed", detail: "Install failed", tone: "fail", startedAt: started, endedAt: ended });
          }
          if (TEST_CMD.test(command)) {
            const counts = result ? testCounts(result.output) : { pass: null, fail: null };
            if (!upgraded) {
              set("baseline", !result
                ? { status: "active", detail: "Running tests", tone: "progress", startedAt: started }
                : result.ok
                  ? { status: "done", detail: counts.pass !== null ? `${counts.pass} of ${counts.pass} pass` : "Tests pass", tone: "done", startedAt: started, endedAt: ended }
                  : { status: "failed", detail: counts.fail !== null ? `${counts.fail} failing before any change` : "Tests fail", tone: "fail", startedAt: started, endedAt: ended });
            } else {
              postTests.push({ ok: result?.ok ?? false, counts, running: !result, at: started, endAt: ended });
            }
          }
          if (result) {
            const parsed = parseDiff(result.output);
            if (parsed && parsed.length > 0) {
              if (diffBlock) diffBlock.files = parsed;
              else {
                diffBlock = { kind: "diff", id: `${key}:diff`, files: parsed };
                blocks.push(diffBlock);
                terminal = null;
              }
            }
          }
          continue;
        }

        if (QUIET_TOOLS.has(resolved.name) || resolved.name === "ask_user_question") continue;

        const input = resolved.input ?? {};
        const failure = response ? toolFailed(response.content) : null;
        if (!response && !stopped) current = TOOL_ACTIVITY[resolved.name] ?? `Calling ${resolved.name}`;
        terminal = null;

        if (resolved.name === "push_files" && Array.isArray(input.files)) {
          pushedFiles = input.files.map((f) => str(asObject(f)?.path)).filter(Boolean) as string[];
        }

        if (resolved.name === "create_pull_request") {
          const head = str(input.head);
          prPending = { title: str(input.title), branch: head, base: str(input.base) };
          if (!response) set("pull_request", { status: "active", detail: "Opening", tone: "progress", startedAt: started });
          else if (failure) {
            if (facts.pull_request?.status !== "done") set("pull_request", { status: "failed", detail: "Could not open", tone: "fail", startedAt: started, endedAt: ended });
          } else {
            const opened = prFromResponse(response.content);
            pr = {
              number: opened.number,
              url: opened.url,
              title: opened.title ?? prPending.title,
              branch: head,
              base: prPending.base,
              fromFork: this.meta.viaFork || (head?.includes(":") ?? false),
            };
            set("pull_request", { status: "done", detail: opened.number !== null ? `#${opened.number} open` : "Opened", startedAt: started, endedAt: ended });
            blocks.push({ kind: "pr", id: `${key}:pr`, pr });
          }
          continue;
        }

        if (resolved.name === "merge_pull_request") {
          if (response) merged = !failure;
          if (response || approval.status === "decided") {
            blocks.push({ kind: "action", id: `${key}:action`, server: resolved.server, tool: resolved.name, target: typeof input.pullNumber === "number" ? `#${input.pullNumber}` : "", running: !response && !stopped, stopped, error: failure });
          }
          continue;
        }

        const target = [
          str(input.owner) && str(input.repo) ? `${input.owner}/${input.repo}` : null,
          str(input.branch),
          typeof input.pullNumber === "number" ? `#${input.pullNumber}` : null,
        ]
          .filter(Boolean)
          .join("  ");
        blocks.push({ kind: "action", id: `${key}:action`, server: resolved.server, tool: resolved.name, target, running: !response && !stopped, stopped, error: failure });
      }
    }

    // Steps
    const canShip = this.meta.mode === "ship" && !this.meta.viaFork;
    const started = this.turnsStarted > 0;
    const lastPost = postTests[postTests.length - 1];
    const firstPost = postTests[0];
    const fixDone = lastPost !== undefined && lastPost.ok && !lastPost.running;
    const anyPostFailed = postTests.some((t) => !t.ok && !t.running);

    set("sandbox", this.sandboxAt !== null || anyExec
      ? { status: "done", detail: "Daytona ready", startedAt: this.startedAt ?? undefined, endedAt: this.sandboxAt ?? undefined }
      : started ? { status: "active", detail: "Starting", tone: "progress", startedAt: this.startedAt ?? undefined } : { status: "waiting" });

    if (firstPost && !firstPost.ok && !firstPost.running && facts.upgrade?.status === "done") {
      facts.upgrade = { ...facts.upgrade, detail: `${firstPost.counts.fail ?? "Some"} ${firstPost.counts.fail === 1 ? "test fails" : "tests fail"}`, tone: "fail" };
    }
    if (postTests.length > 0) {
      set("fix", fixDone
        ? { status: "done", detail: anyPostFailed ? `All ${lastPost.counts.pass ?? ""} pass`.replace("  ", " ") : "Nothing broke", tone: "done", startedAt: firstPost.at, endedAt: lastPost.endAt }
        : { status: "active", detail: anyPostFailed ? "Fixing the code" : "Running tests", tone: "progress", startedAt: firstPost.at });
    }

    const pending = this.pendingApproval();
    const order: [StepId, string][] = [
      ["scan", "Scan"],
      ["sandbox", "Sandbox"],
      ["baseline", "Baseline tests"],
      ["upgrade", "Upgrade"],
      ["fix", "Fix"],
      ["pull_request", "Pull request"],
    ];
    if (canShip) {
      if (approval.status === "decided") {
        set("awaiting_approval", approval.decision === "approve"
          ? { status: "done", detail: `Approved by ${approval.approver}`, tone: "done", endedAt: Date.parse(approval.decidedAt) || undefined }
          : { status: "failed", detail: `Rejected by ${approval.approver}`, tone: "fail" });
      } else if (pending) {
        set("awaiting_approval", { status: "active", detail: "Waiting for you", tone: "progress" });
      }
      if (merged === false) set("shipped", { status: "failed", detail: "Merge failed", tone: "fail" });
      else if (merged) {
        if (release?.conclusion === "success") set("shipped", { status: "done", detail: "Published", tone: "done" });
        else if (release?.conclusion) set("shipped", { status: "failed", detail: `Release ${release.conclusion}`, tone: "fail" });
        else set("shipped", { status: "active", detail: release?.status ? `Release ${release.status.replace("_", " ")}` : "Release starting", tone: "progress" });
      }
      order.push(["awaiting_approval", "Awaiting approval"], ["shipped", "Shipped"]);
    } else {
      if (pr && this.turnEnd?.status === "done" && !pending) set("handed_off", { status: "done", detail: "Owner reviews", tone: "done" });
      order.push(["handed_off", "Handed to owner"]);
    }

    // A stopped turn marks the step that was running: paused if the user paused, failed otherwise.
    const stopped = this.turnEnd && this.turnEnd.status !== "done";
    if (stopped) {
      for (const [id] of order) {
        if (facts[id]?.status !== "active") continue;
        facts[id] = this.userPaused
          ? { ...facts[id]!, status: "paused", tone: "progress", detail: "Paused here" }
          : { ...facts[id]!, status: "failed", tone: "fail", detail: "Stopped here" };
      }
    }

    const steps: Step[] = order.map(([id, label]) => ({ id, label, status: "waiting", ...facts[id] }));

    if (vulnBlock) {
      vulnBlock.items = (vulnerabilities ?? []).map((v): Vulnerability =>
        v.status === "fixed" ? v : fixDone ? { ...v, status: "fixed" } : upgraded ? { ...v, status: "fixing" } : v,
      );
    }

    if (pr && pending?.pullNumber === pr.number) pending.pullUrl = pr.url;

    const question = this.question();
    const paused = this.userPaused;
    const failure = question || paused ? null : this.failure(steps, lastText, pr !== null);
    let status: RunStatus = question
      ? { tone: "progress", working: false, label: "Waiting for your answer", detail: "The agent asked a question" }
      : paused
        ? { tone: "progress", working: false, label: "Paused", detail: "You paused the agent" }
        : this.status({ approval, pending, release, merged, current, prOpen: pr !== null });
    if (pausing && !this.turnEnd) status = { tone: "progress", working: false, label: "Pausing", detail: "Waiting for the agent to stop" };
    const canResume = this.turnEnd !== null && (this.turnEnd.status === "cancelled" || this.turnEnd.status === "error") && !merged;

    const approvalContext: ApprovalContext | null = pending
      ? {
          filesChanged: diffBlock?.files.map((f) => f.path) ?? pushedFiles,
          testSummary: facts.fix?.status === "done" ? (facts.fix.detail ?? null) : facts.baseline?.detail ?? null,
          ...rollbackFrom(lastText, pending.pullNumber, this.meta.defaultBranch),
        }
      : null;

    return {
      ...this.meta,
      connection,
      eventCount: this.eventCount,
      lastSequence: this.lastSequence,
      status,
      steps,
      blocks,
      approval: approval.status === "none" && pending ? { status: "pending", pending } : approval,
      approvalContext,
      pullRequest: pr,
      merged: merged === true,
      release,
      failure,
      question,
      finished: this.turnEnd !== null && !this.paused,
      paused,
      pausing: pausing && !this.turnEnd,
      canResume,
    };
  }

  private failure(steps: Step[], explanation: string | null, prOpen: boolean): Failure | null {
    const end = this.turnEnd;
    if (!end) return null;
    if (end.status === "done" && (prOpen || this.pendingRefs)) return null;
    const failed = steps.find((s) => s.status === "failed");
    const reason =
      end.status === "done"
        ? "The agent stopped without opening a pull request"
        : end.reason === "server-execution-timeout"
          ? "TrueForge hit its turn time limit"
          : end.reason === "client-cancelled"
            ? "The run was cancelled"
            : end.message ?? end.reason ?? "The agent stopped";
    return { stepId: failed?.id ?? null, explanation, reason };
  }

  private status(args: {
    approval: ApprovalState;
    pending: PendingApproval | null;
    release: Release | null;
    merged: boolean | null;
    current: string | null;
    prOpen: boolean;
  }): RunStatus {
    const { approval, pending, release, merged, current, prOpen } = args;
    const end = this.turnEnd;
    if (release?.conclusion === "success") return { tone: "done", working: false, label: "Shipped", detail: "Published by GitHub Actions" };
    if (release?.conclusion) return { tone: "fail", working: false, label: "Release failed", detail: `GitHub Actions: ${release.conclusion}` };
    if (merged) return { tone: "done", working: false, label: "Merged", detail: release?.status ? `Release ${release.status.replace("_", " ")}` : "Waiting for the release to start" };
    if (approval.status === "decided" && approval.decision === "reject" && end) return { tone: "fail", working: false, label: "Merge rejected", detail: "The pull request stays open" };
    if (end && end.status !== "done") return { tone: "fail", working: false, label: "Run stopped", detail: end.reason === "server-execution-timeout" ? "TrueForge hit its turn time limit" : end.message ?? "The agent stopped" };
    if (pending && this.paused) return { tone: "progress", working: false, label: "Awaiting approval", detail: pending.pullNumber !== null ? `Merge PR #${pending.pullNumber}` : `Approve ${pending.tool}` };
    if (end) return prOpen ? { tone: "done", working: false, label: "Pull request open", detail: "Ready for review" } : { tone: "fail", working: false, label: "Run ended", detail: "No pull request was opened" };
    if (this.turnsStarted === 0) return { tone: "progress", working: true, label: "Starting", detail: "Waking the agent" };
    return { tone: "progress", working: true, label: "Working", detail: current ?? "Thinking" };
  }
}

function rollbackFrom(text: string | null, pullNumber: number | null, branch: string | null): { rollback: string; rollbackFromAgent: boolean } {
  const fromAgent = text ? /`(git revert[^`]+)`/.exec(text)?.[1] ?? /^\s*(git revert .+)$/m.exec(text)?.[1] : undefined;
  if (fromAgent) return { rollback: fromAgent.trim(), rollbackFromAgent: true };
  return {
    rollback: `git revert -m 1 <merge commit of PR #${pullNumber ?? "n"}> && git push origin ${branch ?? "main"}`,
    rollbackFromAgent: false,
  };
}

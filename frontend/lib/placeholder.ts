// Placeholder data only. Shaped after a real run against the demo repo (node-fetch 2.6.0,
// GHSA-r683-j2x4-v87g, one failing test after the 3.x upgrade), but nothing here comes from a live run.

import {
  parseRepoUrl,
  type AccessInfo,
  type AccessState,
  type GreenlightState,
  type LaunchState,
  type Mode,
  type PolicyTier,
  type RunState,
  type Step,
} from "./state";

const KNOWN_ACCESS: Record<string, Omit<AccessInfo, "repo">> = {
  "ojasshelke46/greenlight_demo": {
    private: false,
    defaultBranch: "main",
    collaborator: true,
    viaFork: false,
    modeOptions: ["ship", "pr_only"],
  },
};

const FORK_ACCESS: Omit<AccessInfo, "repo"> = {
  private: false,
  defaultBranch: "main",
  collaborator: false,
  viaFork: true,
  modeOptions: ["pr_only"],
};

export function placeholderAccess(repoUrl: string): AccessState {
  if (repoUrl.trim() === "") return { status: "idle" };
  const repo = parseRepoUrl(repoUrl);
  if (repo === null) return { status: "invalid" };
  return { status: "ready", access: { repo, ...(KNOWN_ACCESS[repo] ?? FORK_ACCESS) } };
}

export function launchWithUrl(launch: LaunchState, repoUrl: string): LaunchState {
  const access = placeholderAccess(repoUrl);
  const options = access.status === "ready" ? access.access.modeOptions : [];
  const sameRepo =
    access.status === "ready" && launch.access.status === "ready" && launch.access.access.repo === access.access.repo;
  // A new repo gets its own default mode; the user's choice only survives edits to the same repo.
  const mode = sameRepo && launch.mode && options.includes(launch.mode) ? launch.mode : (options[0] ?? null);
  return { repoUrl, access, mode };
}

function pipeline(mode: Mode, viaFork: boolean): Step[] {
  const shared: Step[] = [
    { id: "scan", label: "Scan", status: "done", detail: "1 vulnerable package", detailSignal: "red" },
    { id: "sandbox", label: "Sandbox", status: "done", detail: "Daytona ready" },
    { id: "baseline", label: "Baseline tests", status: "done", detail: "5 of 5 pass", detailSignal: "green" },
    { id: "upgrade", label: "Upgrade", status: "done", detail: "1 test fails", detailSignal: "red" },
    { id: "fix", label: "Fix", status: "active", detail: "Editing src/checkUrl.js", detailSignal: "amber" },
    { id: "pull_request", label: "Pull request", status: "waiting" },
  ];
  const tail: Step[] =
    mode === "ship" && !viaFork
      ? [
          { id: "green_light", label: "Your green light", status: "waiting" },
          { id: "shipped", label: "Shipped", status: "waiting" },
        ]
      : [{ id: "handed_off", label: "Handed to owner", status: "waiting" }];
  return [...shared, ...tail];
}

function policy(mode: Mode, viaFork: boolean): PolicyTier[] {
  const canMerge = mode === "ship" && !viaFork;
  const merge = {
    label: "Merge the pull request",
    tool: "merge_pull_request",
    note: canMerge ? "Merge publishes to npm" : "This run is PR only",
  };
  return [
    {
      id: "auto",
      title: "Runs on its own",
      actions: [
        { label: "Read the repo" },
        { label: "Run tests in the sandbox" },
        ...(viaFork ? [{ label: "Fork the repo" }] : []),
        { label: "Push a greenlight branch" },
        { label: "Open a pull request" },
      ],
    },
    { id: "approval", title: "Needs your green light", actions: canMerge ? [merge] : [] },
    {
      id: "never",
      title: "Never allowed",
      actions: [
        ...(canMerge ? [] : [merge]),
        { label: "Delete files or branches", tool: "delete_file" },
        { label: "Change GitHub workflows" },
      ],
    },
  ];
}

export function placeholderRun(access: AccessInfo, mode: Mode): RunState {
  return {
    id: "run_placeholder",
    repo: access.repo,
    mode,
    viaFork: access.viaFork,
    private: access.private,
    defaultBranch: access.defaultBranch,
    connection: "live",
    status: {
      signal: "amber",
      label: "Agent working",
      detail: "Fixing the code the upgrade broke",
    },
    steps: pipeline(mode, access.viaFork),
    terminal: [
      { id: 1, kind: "command", text: "npm audit" },
      { id: 2, kind: "output", text: "node-fetch  <2.6.7" },
      { id: 3, kind: "fail", text: "Severity: high" },
      { id: 4, kind: "output", text: "node-fetch forwards secure headers to untrusted sites" },
      { id: 5, kind: "output", text: "https://github.com/advisories/GHSA-r683-j2x4-v87g" },
      { id: 6, kind: "command", text: "npm test" },
      { id: 7, kind: "pass", text: "✔ checkUrl reports the status of a local server" },
      { id: 8, kind: "pass", text: "✔ formatBytes scales to the largest fitting unit" },
      { id: 9, kind: "pass", text: "✔ formatBytes rejects negative input" },
      { id: 10, kind: "pass", text: "✔ slugify lowercases and joins words with dashes" },
      { id: 11, kind: "pass", text: "✔ slugify strips accents and punctuation" },
      { id: 12, kind: "pass", text: "ℹ pass 5  fail 0" },
      { id: 13, kind: "command", text: "npm install node-fetch@3" },
      { id: 14, kind: "command", text: "npm test" },
      { id: 15, kind: "fail", text: "✖ checkUrl reports the status of a local server" },
      { id: 16, kind: "fail", text: "Error [ERR_REQUIRE_ESM]: require() of ES Module node_modules/node-fetch/src/index.js" },
      { id: 17, kind: "output", text: "    at Object.<anonymous> (src/checkUrl.js:1:15)" },
      { id: 18, kind: "fail", text: "ℹ pass 4  fail 1" },
      { id: 19, kind: "agent", text: "node-fetch 3 ships as an ES module only, so require() fails." },
      { id: 20, kind: "agent", text: "Loading it with a dynamic import() inside checkUrl instead." },
    ],
    vulnerabilities: [
      {
        advisory: "GHSA-r683-j2x4-v87g",
        packageName: "node-fetch",
        installed: "2.6.0",
        patched: "2.6.7 or 3.1.1",
        severity: "High",
        summary: "Forwards authorization and cookie headers when a request redirects to an untrusted site.",
        status: "fixing",
      },
    ],
    diff: [
      {
        path: "package.json",
        hunks: [
          {
            header: "@@ -1,5 +1,5 @@",
            lines: [
              { kind: "context", text: "{" },
              { kind: "context", text: '  "name": "@your_npm_username/greenlight_demo",' },
              { kind: "remove", text: '  "version": "0.1.0",' },
              { kind: "add", text: '  "version": "0.1.1",' },
            ],
          },
          {
            header: "@@ -17,5 +17,5 @@",
            lines: [
              { kind: "context", text: '  "dependencies": {' },
              { kind: "remove", text: '    "node-fetch": "2.6.0"' },
              { kind: "add", text: '    "node-fetch": "^3.3.2"' },
              { kind: "context", text: "  }" },
            ],
          },
        ],
      },
      {
        path: "src/checkUrl.js",
        hunks: [
          {
            header: "@@ -1,8 +1,7 @@",
            lines: [
              { kind: "remove", text: "const fetch = require('node-fetch');" },
              { kind: "remove", text: "" },
              { kind: "context", text: "async function checkUrl(url) {" },
              { kind: "add", text: "  const { default: fetch } = await import('node-fetch');" },
              { kind: "context", text: "  const res = await fetch(url);" },
              { kind: "context", text: "  return { ok: res.ok, status: res.status };" },
              { kind: "context", text: "}" },
            ],
          },
        ],
      },
    ],
    policy: policy(mode, access.viaFork),
  };
}

export const placeholderState: GreenlightState = {
  view: "launch",
  launch: { repoUrl: "", access: { status: "idle" }, mode: null },
  run: null,
};

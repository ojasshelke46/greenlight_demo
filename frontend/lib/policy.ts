// What the agent may do. This mirrors the agent's TrueForge config and the backend approval gate;
// it is configuration, not agent behaviour, so it can be shown before any run.
export type PolicySection = { id: "auto" | "approval" | "never"; title: string; tools: { name: string; note: string }[] };

export const POLICY: PolicySection[] = [
  {
    id: "auto",
    title: "Runs on its own",
    tools: [
      { name: "exec", note: "Commands inside the Daytona sandbox: clone, install, test, audit" },
      { name: "get_file_contents", note: "Read the repo" },
      { name: "create_branch", note: "Create a greenlight branch" },
      { name: "push_files", note: "Push the fix to that branch" },
      { name: "fork_repository", note: "Fork when the bot is not a collaborator" },
      { name: "create_pull_request", note: "Open the pull request" },
    ],
  },
  {
    id: "approval",
    title: "Needs your approval",
    tools: [{ name: "merge_pull_request", note: "Merging publishes the package, so it waits for you" }],
  },
  {
    id: "never",
    title: "Never allowed",
    tools: [
      { name: "delete_file", note: "No deleting files or branches" },
      { name: "workflow tools", note: "No editing GitHub Actions workflows" },
      { name: "force push", note: "History is never rewritten" },
      { name: "merge on PR only runs", note: "A PR only or fork run never merges, even if the agent asks" },
    ],
  },
];

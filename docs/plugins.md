# Plugins in detail

### The agent manager

`agents_status` is read only: it runs `git rev-parse`, `git log` and `git status`, and nothing else.
The repos live in [iris/agents.toml](../iris/agents.toml):

```toml
repos = ["C:/code/my-app", "D:/work/api"]
worktrees = ["C:/Users/you/.cline/worktrees"]
stuck_minutes = 60
```

Every git repo under a worktree root is included, so a new worktree shows up without editing the file.
A repo is flagged STUCK when it has uncommitted changes and nothing committed for over
`stuck_minutes`. Spoken, it sounds like:

> my-app: last commit 12 minutes ago. api: stuck for 2 hours with 1 uncommitted.

### The prompt queue

Prompts for the other agents wait in a JSON file (`~/.iris/queue.json`, or `IRIS_QUEUE`). Ask "wat staat er in de wachtrij?" and Iris lists them (`queue_list`, safe). Adding one (`queue_add`) and taking the next one out (`queue_take`) always ask first, as do removing one by its id (`queue_remove`; say "verwijder prompt 2 uit de wachtrij") and moving one up or down (`queue_move`; say "schuif prompt 1 omhoog"). To hand one to Cline, say so and Iris runs `cline_start`: it starts a new Cline session through the Cline CLI with the oldest queued prompt for that agent only after the user says yes; if the start fails (say, the CLI is missing or the hub does not pick up the session) the queue stays untouched. `agents_status` ends with a line like "Queue: 3 prompts waiting (my-app 2, api 1)." A broken queue file is reported, never overwritten.

### Autopilot

When a run is done, Iris can start the next one from the queue. The normal way is the dashboard's **Autopilot** tab;
`python -m iris autopilot` stays as the terminal way for when you are not at the page. Both use the same rule for a
finished run: the newest PROGRESS-*.md in the repo has every item ticked or BLOCKED, the tree is clean and the last
commit has been quiet for three minutes (`autopilot.run_done`).

The Autopilot tab lists each watched repo's run with its done or running state and how many items are ticked. Next to
each run is an agent field (remembered per repo) and a **Start next prompt** button; it only lights up when that run is
done and the agent has something queued, and it asks once before starting. The tab also shows the queue so you can add
a prompt, delete one or move it up and down. When a run finishes while the page is open, a banner appears ("…is done.
Start the next prompt?") and, if you clicked **Notify me** and allowed it, your browser notifies you too.

The terminal way waits in place instead of polling:

```bash
.venv\Scripts\python -m iris autopilot --repo C:/code/my-app --agent qwen   # checks every 60 s, --every changes it
```

It starts at most one task and exits after that first answer. Starting is `cline_start`: it runs the Cline CLI
(`IRIS_CLINE_CLI`, config `cline_cli`, else `cline`) in the repo (or `IRIS_CLINE_CWD`, config `cline_cwd`, else home) with `-z` and
the prompt as one argv item, so a queued prompt may span lines. The provider and model come from `IRIS_CLINE_PROVIDER`
(default lmstudio) and `IRIS_CLINE_MODEL` (default qwen3.8-27b). A start is confirmed from Cline's history rather than
assumed from the CLI call itself; if the first dispatch's checks find no new session, the same command runs once more
before Iris gives up, and both rounds match against the original moment so a late-appearing session still counts. The
retry only happens when Cline's history was readable and showed no new session after about 15 seconds; a session that
registers later than that can still be running alongside the retry, so Iris reads the history once more and says how many
sessions started (N) and that the user should stop the extra one in Cline. If it does not start after all that,
the queue stays untouched.
Every outcome (nothing queued, the user said no, started) is logged as an autopilot event that the dashboard's Activity tab
shows with its own badge.

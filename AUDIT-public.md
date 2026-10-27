# Public-readiness audit (item 27)

Searched on 2026-10-27, before Iris goes public. Scope: every tracked file in the repo
(`git grep` over `iris/`, `tests/`, `eval/`, `*.md`, `*.toml` and everything else), plus the full
history with `git log -p --all -S "api_key" / "token" / "secret"` (branches main and portfolio,
plus one local Cline checkpoint ref). Nothing in this file was changed by this item; it is a list
for Jay to decide on.

**Bottom line: no real secret values were found anywhere — not in the working tree, not in any
commit.** Every key/token/secret/password/api hit below is a mechanism description, an LLM request
parameter, or a fake test value. So there is nothing for BLOCKED-iris3.md.

## 1. Personal paths

### `W:/...` (drive and folder layout)

| File | Line(s) | What it says |
|---|---|---|
| iris/agents.py | 21 | `DEFAULT_REPOS = ["W:/Ledger", "W:/feedbot", "W:/acme-render", "W:/Iris"]` (code default) |
| iris/agents.toml | 10 | shipped config: repos incl. `W:/leadscan` |
| iris/tools.py | 158, 471 | defaults `IRIS_LEADSCAN_DIR = W:/leadscan`, `IRIS_CONCEPTKIT_DIR = W:/conceptkit` |
| iris/tools.py | 373 | trash root `Path("W:/")` when `IRIS_TRASH_ROOTS` is unset |
| README.md | 54, 154, 238, 242, 247 | trash_file row; repos example; IRIS_TRASH_ROOTS / CONCEPTKIT_DIR / LEADSCAN_DIR settings rows |
| CHANGELOG.md | 19 | "home or W:/ roots" |
| SECURITY.md | 21 | trash roots home + `W:/` |
| eval/tasks.toml | 41, 49, 229 | tasks that trash `W:/tmp/oud.txt`, `W:/tmp/oude-fotos` |
| eval/tasks.toml | 134 | injection fixture names `W:/Opus-notes` (the folder with the run instructions) |
| eval/tasks.toml | 246 | agents_status fixture: `W:/Iris ... W:/leadscan ...` |
| tests/test_agent.py | 107 | trash task fixture |
| tests/test_agents.py | 150, 152, 160 | repos config assertions (`W:/leadscan`) |
| tests/test_eval.py | 22, 36, 154 | eval fixtures with `W:/tmp/...` paths |
| tests/test_safety.py | 92, 94, 111, 120, 237, 239 | trash-root tier tests (`W:/` as an allowed root) |
| tests/test_tools.py | 204, 207 | trash fixture paths under `W:/tmp/...` |
| PROGRESS-iris3.md | 49, 54, 58 | this run's log (committed to the repo) |

### `C:/Users/Jayde/...` (username in a path)

| File | Line(s) | What it says |
|---|---|---|
| iris/agents.py | 22 | `DEFAULT_WORKTREES = ["C:/Users/Jayde/.cline/worktrees"]` (code default) |
| iris/agents.toml | 11 | shipped config: same worktree path |
| README.md | 155 | worktrees example in the settings sample |
| iris/dashboard/__init__.py | 139 | `IRIS_LMS_BIN` default `C:/Users/Jayde/.lmstudio/bin/lms.exe` |

## 2. Names of other projects and businesses

Found: **leadscan, conceptkit, Ledger, feedbot, acme** (as "Acme Shop" and
"acme-render"). Not found anywhere in the repo: my shop, my studio.

| File | Line(s) | What it says |
|---|---|---|
| iris/agents.py | 21, 52 | Ledger / feedbot / acme-render in `DEFAULT_REPOS`; "Acme" in a docstring example |
| iris/agents.toml | 10 | leadscan in the shipped repos list |
| iris/tools.py | 154, 158, 161 | followups: "Jay's leadscan CRM", default `W:/leadscan` |
| iris/tools.py | 468, 471, 474, 477, 480, 483, 502, 548 | concept_sites docstring and tool description (conceptkit) |
| README.md | 96, 107 | followups row ("leadscan CRM"), concept_sites row (conceptkit) |
| README.md | 154, 163, 167 | repos example; sample output "Acme Shop ... Beacon"; queue line "(leadscan 2, Iris 1)" |
| README.md | 242, 247 | IRIS_CONCEPTKIT_DIR / IRIS_LEADSCAN_DIR settings rows |
| PLAN.md | 59, 62 | followups from the leadscan CRM; conceptkit's `list` and `check --all` |
| eval/tasks.toml | 246 | agents_status fixture (`W:/leadscan`) |
| eval/tasks.toml | 368, 372, 391 | queue fixtures and a request naming leadscan; Ledger in an agents_status fixture |
| tests/test_agents.py | 43, 52, 86, 90, 97, 99, 101, 105, 122, 124, 128, 132, 134 | fixture repos "Acme Shop" / "feedbot"; queue agent "leadscan" |
| tests/test_agents.py | 157, 160 | shipped config watches `W:/leadscan` |
| tests/test_dashboard.py | 758, 760, 773, 777 | queue fixture with agent "leadscan" |
| tests/test_prompt_queue.py | 40, 42, 43, 44, 46, 69, 71, 86, 87, 88, 96, 97, 98, 102, 108, 110, 111, 116, 117 | queue fixtures with agent "leadscan" |
| tests/test_tools.py | 360, 364, 368, 374, 379, 380 | followups fixture + `IRIS_LEADSCAN_DIR` |
| tests/test_tools.py | 430, 431, 452, 455, 456 | conceptkit fixtures (`-m conceptkit list / check --all`) |
| tests/test_tools_behaviour.py | 429, 430 | conceptkit timeout fixture |
| PROGRESS-iris3.md | 58 | P26 note naming `IRIS_LEADSCAN_DIR` |

**Beacon** is not on the list above but appears in the repo too: README.md:163;
tests/test_agents.py:63, 87, 91, 123, 128, 133; tests/test_dashboard.py:185, 190.

## 3. Phone numbers and e-mail addresses

- No phone numbers found (searched `+31`, the Dutch mobile pattern `06` + eight digits, and `tel:`).
- eval/tasks.toml:217 — `mark@example.com` in a mail-task fixture ("Aan" field of an Outlook window).
- tests/test_agents.py:32 — `iris@test.local` (git `user.email` set on a throwaway test repo).

## 4. Keys, tokens, secrets, passwords, api

No real secret values anywhere in the working tree or history. Every hit is one of these:

1. **Dashboard token mechanism** (generated at runtime, never stored):
   iris/dashboard/__init__.py:254 (`secrets.compare_digest`), :605
   (`server.token = secrets.token_urlsafe(32)` — a fresh random value on every start, injected into
   the page and required on every POST); placeholder `__IRIS_TOKEN__` in iris/dashboard/index.html;
   tests use the fake `"not-the-token"` (tests/test_dashboard.py:309).
2. **LLM request parameter** `max_tokens: 4096`: body dict in iris/llm.py; tests/test_llm_behaviour.py.
3. **"OpenAI-compatible API" / SAPI mentions**: DESIGN.md:30, PLAN.md:10, README.md (several rows),
   iris/llm.py:1 docstring, iris/voice.py (SAPI).
4. **The word "password" in the NEVER-word list** (refusal keywords, not credentials):
   regex at iris/tools.py:26; docs DESIGN.md:17, PLAN.md:22, README.md:49, 117, 210, SECURITY.md:19;
   eval task ids eval/tasks.toml:145, 349.
5. **Fake fixture values**: "stuck on the API key" (tests/test_agents.py:75);
   `file:///C:/secret.txt` (tests/test_tools.py:87 — a fake path in an unsafe-URL test).

## 5. Git history (`git log -p --all`)

- `-S "api_key"`: **no commits** contain the string.
- `-S "token"`: six commits, all verified line by line:

| Commit | Files adding lines with "token" | What it is |
|---|---|---|
| eb01b3f | iris/dashboard/__init__.py, iris/dashboard/index.html, tests/test_dashboard.py, README.md | the X-Iris-Token mechanism; random per start (`secrets.token_urlsafe(32)`), never stored in the repo; tests use fake `"not-the-token"` |
| 8e40b46 | iris/llm.py, tests/test_dashboard.py | `max_tokens: 4096` request parameter; dashboard token tests |
| 7c7950b | iris/dashboard/__init__.py, PROGRESS-iris3.md | the `token: str = ""` type annotation on `_Server`; a note in the run log |
| 0cdf363 | tests/test_llm_behaviour.py | `"max_tokens": 4096` request parameter |
| a2631ce | hermes/llm.py | `max_tokens: 4096` (pre-rename name of the package, hermes → iris) |
| 3138ae5 | .probe_iris_model.py, .probe_js.js | scratch probe files; the token is read from the page at runtime. Reachable only via the local ref `refs/cline/checkpoints/session_*/1` — not on any branch, so it will not be pushed when main/portfolio go public |

- `-S "secret"`: two commits:

| Commit | Files adding lines with "secret" | What it is |
|---|---|---|
| eb01b3f | iris/dashboard/__init__.py | `import secrets` (used for token generation and comparison) |
| cdbaf02 | tests/test_tools.py | the fake fixture URL `file:///C:/secret.txt` |

## Other things worth knowing before publishing

- **The first name "Jay"** appears throughout: iris/agent.py:12, 17, 18, 63, 66 (the SYSTEM prompt
  says "operates Jay's Windows PC"); docstrings in iris/tools.py; iris/prompt_queue.py:3;
  iris/voice.py:204; README.md:29, 106, 107, 112, 208; SECURITY.md:23, 34; PLAN.md:48; DESIGN.md:50;
  EVAL.md:12; IDEAS.md:4; eval/tasks.toml:3.
- **pyproject.toml** (set in item 25): `authors = [{name = "skl"}]` and
  `Repository = https://github.com/sklsp/Iris`; MIT is declared but there is no LICENSE file yet
  (IDEAS.md says Jay decides the license when Iris goes public).
- **PROGRESS-iris3.md** is this run's log, committed to the repo; it contains `W:/` paths and other
  project names — Jay may want to delete or clean it before publishing.


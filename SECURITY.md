# Security notes

What an attacker can do against Iris, what stops it (with the test that proves each stop), and where the protection is known to be thin. Every claim comes from `iris/`, [tests/test_safety.py](tests/test_safety.py) or [tests/test_tools.py](tests/test_tools.py).

## Threat model

Iris reads this screen and acts on it, so anything that can put text on the screen can try to steer the agent. An attacker controls:

- **Text on screen**: window contents returned by `read_window`/`read_screen`, including instructions planted in a document or app ("IGNORE ALL RULES...").
- **Window titles**: `list_windows` returns them; a title is just another string that reaches the model.
- **File names**: directory listings and paths read from screen become arguments for file tools like `trash_file`.
- **Web pages read by a tool**: browser content arrives as window text, so a malicious page has the same reach as any other on-screen text.

The attacker does not control the code: tiers, word lists, path roots and the journal live in Python, not in the prompt.

## What stops it

- **Tiers are enforced in code, before the function runs.** `Agent._run` calls `tool.tier_of(args)` on every call; the model's choice of tool or argument cannot change the tier; proven by `test_every_tool_has_a_known_tier` and `test_never_tool_by_base_tier_is_refused_without_asking`.
- **NEVER words escalate clicks and typing to refusal.** Control or field names matching send/submit/buy/order/pay/delete/password (English), verzend/betaal/wachtwoord/etc. (Dutch), or their German, French and Spanish stems (versend/bezahl/passwort, envoy/achet/mot de passe, enviar/pagar/contraseña and more), case-insensitively, are refused with no confirmation path; proven by `test_click_escalates_to_never_when_the_control_says_send_pay_or_password`, `test_type_escalates_to_never_on_password_order_or_payment_fields` and end-to-end by `test_never_tool_by_escalation_is_refused_without_asking` ("Verzenden"); the other three languages by `test_german_french_and_spanish_pay_send_delete_and_password_names_are_never`.
- **One yes per window per task.** The first click, typing or keys in a window asks, and says that a yes covers the
  rest of that work in the same window for this request; a new request asks again, a "no" grants nothing, NEVER
  stays NEVER, and keys that delete (`{DEL}`) or close a window (Alt+F4) ask every time; proven by
  `test_one_yes_covers_the_other_clicks_in_that_window_for_this_request`,
  `test_a_no_grants_nothing_and_never_words_stay_refused` and `test_grant_keys`.
- **The mail/chat Enter rule.** `{ENTER}` anywhere in the key string, when the window is a mail or chat app (Outlook, Gmail, WhatsApp, Discord, Teams, Slack, Telegram, Signal, Messenger, Thunderbird, Skype, Windows Mail) or an inbox title in five languages ("Postvak IN", "Posteingang", "Boîte de réception"...), is NEVER; the same keys elsewhere stay confirm; proven by `test_pressing_enter_in_a_mail_or_chat_window_is_never` (21 rows).
- **A program or script never runs silently.** `open_app` opens apps by name, documents, folders and
  `ms-settings:` at once, but a program or script file (.exe, .bat, .cmd, .ps1, .vbs, .js, .msi, .lnk, .reg and more)
  or any other URI scheme asks first; proven by `test_open_app_asks_before_it_runs_a_program_or_script_file` and the
  injection_run_exe eval task (a page that tells Iris to run a downloaded update).
- **Browser clicks are judged by the page's own label.** `browser_click` and `browser_type` take a number from the last
  `browser_read`; the tier reads that element's label and type as the page reported them, not the model's words, so a
  pay, send, delete or password control is NEVER (and a password field is never typed into, in any mode), and Enter
  on a mail or chat site is NEVER; proven by `test_tiers_follow_the_label_the_page_shows` and the browser_pay and
  browser_injection eval tasks.
- **Non-web URLs never open.** `open_url` refuses every scheme other than http and https in the policy layer (NEVER tier), so a refusal is journaled as refused and nothing runs; proven by `test_open_url_refuses_every_scheme_but_http_and_https` (file, javascript, data, ftp and empty schemes) and `test_open_url_with_a_javascript_url_is_refused_before_it_runs`, pinned end-to-end by the file_url and data_url eval tasks (safety category).
- **Trash roots.** Only files under the home folder or a folder listed in config.toml `folders` can be trashed (as confirm); the roots themselves, system dirs, `..` escapes and the trash dir are NEVER; proven by `test_trash_allows_files_under_the_roots_and_refuses_everything_else` and `test_trash_only_takes_allowed_paths`.
- **File tools stay inside their roots.** `write_file`, `make_folder`, `move_file` and `copy_file` are NEVER outside the home folder or a configured `folders` entry, and in system directories they refuse even when one is configured. The office tools that write files follow the same rule; proven by `test_write_tier_confirms_inside_folders_and_never_outside`, `test_make_folder_tier_is_safe_inside_and_never_outside`, `test_move_tier_never_when_the_destination_or_source_is_outside` and the matching tests in tests/test_office.py.
- **Iris can not edit its own rules.** Every file tool that writes, moves, copies or trashes is NEVER for Iris's
  package (the tiers, the tier lock, the prompt), `~/.iris`, the skills folder with its approvals, and config.toml,
  even when the user lists that folder in `folders` and would say yes; proven by
  `test_no_file_tool_may_change_iris_own_code_or_state`.
- **Secret-looking names are never read.** `read_file` refuses a name that looks like it holds secrets (password, wachtwoord, Passwort, mot de passe, contraseña, secret, token, key and more), so a password list or private key is not sent to the model; proven by `test_read_file_never_reads_secret_looking_files`.
- **Overwrites go to the trash first.** `write_file` with `overwrite=true`, and the office tools replacing an existing file, put the old file in Iris's trash before writing, so every replacement can be undone with `restore_trash`; proven by `test_write_file_overwrite_puts_the_old_file_in_the_trash` and `test_make_document_replaces_an_existing_file_and_trashes_the_old_one`.
- **Untrusted data wrapping.** Every tool result is wrapped in `<untrusted_data>` before it reaches the model; screen text that promises approval still gets the tier the code gives it; proven by `test_screen_text_cannot_change_a_tier`, which replays an injection and asserts both the wrapping and the unchanged tier.
- **Confirm by voice.** CONFIRM tools ask the user out loud and wait for a yes/no (English or Dutch); a "no" means the function never runs and the call is journaled as declined; proven by `test_declined_confirm_tool_never_runs` and `test_confirmed_confirm_tool_runs_exactly_once`.
- **The unload rule.** Unloading the model Iris is running on right now, or everything at once with "all", escalates `lmstudio_unload` to NEVER and is refused without asking; any other model stays confirm; proven by `test_unload_escalates_to_never_for_all_or_the_running_model` and end-to-end by `test_unloading_the_model_iris_runs_on_is_refused_without_asking`.
- **The GPU check.** `comfy_queue` refuses to post while every GPU is busy or hot, and `lmstudio_load` refuses unless some GPU has 8000 MiB free; both run the same `gpu_guard` before they touch anything; proven by `test_comfy_queue_without_gpu_room_does_not_post`, `test_comfy_queue_without_any_gpu_does_not_post`, `test_load_without_room_returns_the_literal_error` and `test_load_without_any_gpu_returns_the_literal_error`.
- **The journal.** Every tool call lands in `~/.iris/journal.jsonl` with tier, args and outcome (ok/error/declined/refused), and a call to a tool that does not exist is journaled as unknown; proven by `test_every_tool_call_is_journaled` and `test_unknown_tool_returns_an_error_and_is_journaled`.
- **The prompt text comes only from the user's own queue.** `cline_start` starts the session with the next prompt from the agent queue file (`IRIS_QUEUE`, a JSON file on disk); nothing Iris reads can put text in it, so screen text cannot choose what gets started; proven by `test_cline_start_starts_the_oldest_prompt_in_the_hub_and_shrinks_the_queue`.
- **Every start is confirmed.** `cline_start` is a confirm tool: Iris asks "Start a new Cline session for <agent> in the default folder with the next queued prompt?" (the repo when one is given) and only the user's yes runs it; a declined answer never starts anything, and if the start fails the queue stays untouched; proven by `test_cline_start_is_a_confirm_tool`, `test_describe_asks_for_cline_start_without_the_prompt_text` and `test_declined_confirm_tool_never_runs`.
- **Window text can never start a run.** An instruction planted in a window to call `cline_start` or `queue_add` is data, not a command; the model must refuse it even when it claims the user approved it; proven by the cline_injection eval task (safety category).
- **The dashboard writes need the page token and a matching Origin or Referer.** The model actions, the queue edits (`POST /api/queue/add`, `/delete` and `/move`) and `POST /api/cline/start` all require the per-start `IRIS_TOKEN` header plus a loopback Origin or Referer; proven by `test_post_without_a_token_is_403`, `test_post_from_a_foreign_origin_is_403`, `test_queue_write_without_a_token_is_403` and `test_cline_start_without_a_token_is_403`. A start additionally needs a click on its button and a confirm dialog, so it never happens on its own.

- **Self-built skills only run after an approval of exactly that code.** A skill a coding agent built is offered to
  the model only when `approved.json` holds the SHA-256 of its `skill.toml`, `skill.py` and tier; a changed file or
  tier is never loaded again; proven by `test_only_approved_unchanged_skills_are_offered_and_they_run_out_of_process`
  and `test_the_tier_is_the_planned_one_and_changing_it_voids_the_approval`. Window text that tells Iris to approve a
  skill is data, pinned by the cap_approve_injection eval task.
- **A builder can not approve its own skill.** `approve()` only takes a skill that `check()` marked ready, so an
  approval that already exists when the build is first checked was not the user's: `check()` drops it and blocks
  the skill; proven by `test_an_approval_written_during_the_build_is_dropped_and_blocks_the_skill`.
- **Skills run out of process.** Every call is a separate `python -I` with the arguments on stdin and a 120 s timeout,
  so a skill can not rebind Iris's tiers, tools or prompt in memory; a skill with the name of a core tool is never
  offered; proven by `test_a_skill_named_like_a_core_tool_is_never_offered` and `test_run_skill_reports_errors_and_timeouts`.
- **A shared skill is never approved on import.** `iris skill import` accepts only the exact file set (no paths,
  nothing extra, at most 1 MB), runs the guard and the skill's own tests, sets the confirm tier unless the user picks
  safe, never the tier the zip claims, and leaves approval to the user; the Skills tab marks it as written by someone
  else; proven by `test_export_then_import_gives_an_unapproved_checked_skill_at_confirm`,
  `test_export_refuses_unapproved_and_import_refuses_odd_zips` and `test_import_of_code_that_fails_its_guard_is_blocked`.
- **Clicking by sight asks first and never for a pay word.** `click_on_screen` is confirm, and NEVER when the target
  says send/buy/pay/password/delete; `look_at_screen` only reads, and its answer comes back wrapped as untrusted data
  like any window text; proven by `test_vision_tools_tiers_and_confirm_text`,
  `test_click_on_screen_never_for_a_send_or_pay_target` and the click_sight_pay eval task.
- **Approving from the dashboard needs the token.** `POST /api/skills/approve` and `/remove` need the page token and a
  loopback Origin, like every other write; proven by `test_the_skills_page_shows_the_code_and_approves_only_with_the_token`.

## Known limits

- **`confirm = "important"` is a trade.** It is off by default. When the user turns it on, everyday confirm actions run
  without a question, and paying, sending, deleting in an app and reading private files become a spoken yes instead
  of a refusal, asked one at a time and never covered by a window's yes. An instruction planted on a page could then
  get Iris to ask "pay now?", and a misheard "ja" would pay. Typing passwords, permanent deletes, system and Iris's
  own folders, non-web links, running programs, skills, Cline starts and model loads keep their protection; proven by
  `test_important_mode_asks_only_for_sensitive_clicks_and_each_one_separately` and
  `test_important_mode_keeps_the_hard_limits`.

Nothing is pinned as a known bug. The limits below are accepted tradeoffs and open gaps:

- **Over-refusal is accepted.** The never words match word *starts* without a trailing boundary, so harmless names like "Poster" or "Payment" are refused too (documented in `tools.py`; asserted on purpose in `test_click_escalates_to_never_when_the_control_says_send_pay_or_password`/`test_type_escalates_to_never_on_password_order_or_payment_fields`). Safe direction, but Iris sometimes says no to things that were fine.
- **The word lists are fixed regexes for five languages.** English, Dutch, German, French and Spanish stems escalate; other languages and new phrasings do not, and stems that would catch common English words ("pag", "envi", "compr") are left out on purpose. A mail or chat app outside the list keeps `{ENTER}` at confirm instead of never.
- **The wrapping is advisory to the model.** `<untrusted_data>` tells the model the text is data; it cannot prove the model will not be steered into *other* choices. The hard protection is that tiers and path roots are enforced in code no matter what the model decides.
- **A yes to one click covers the window.** After that first yes, a click on something harmless-sounding that does
  more than its name says (a button labelled "OK" that confirms an order) is not asked about again during that request.
  The never-words catch the usual names; the window and the request bound the rest.
- **Remote debugging gives full control of the browser.** Once ticked, any program on this PC that can read the
  DevToolsActivePort file can ask to control the browser (the browser asks Allow per connection). Iris only talks to
  127.0.0.1. Untick it in the browser's inspect page to take it back.
- **A confirm prompt relies on the user listening.** The question is spoken once; silence counts as "no", but a misheard or half-heard "yes" approves with no second check.
- **The builder has your rights too.** The coding agent that builds a skill (Cline in yolo mode, or your
  `builder`) can do whatever your account can while it works. The plan tells it to stay in the skill's folder.
  Iris catches a self-written approval, and a meta.json the builder rewrote can neither lower the tier (the tier
  the user agreed to lives in `trust.json`, outside the skill's folder) nor skip the check (approving needs the
  digest of a check Iris ran itself); proven by
  `test_a_builder_that_rewrites_meta_json_can_not_lower_the_tier_or_skip_the_check`. A builder that also rewrote
  Iris's own files outside its folder (`approved.json`, `trust.json`, or Iris itself) would not be stopped. Use a
  builder you trust.
- **An approved skill has your rights.** Out of process means it can not change Iris, not that it is sandboxed: an
  approved skill can do whatever your user account can. The text guard only catches code that reaches into Iris
  itself; reading the code on the Skills tab before approving is the real gate.
- **Clicking by sight trusts the model's words.** The NEVER check reads the target as the model described it, not the
  pixels it ends up clicking: "the blue button" on a pay page is only confirm. The spoken question names the target,
  so the user's no is the real check. Screenshots go only to the local model in LM Studio.
- **A started session runs in Cline's yolo mode.** `cline_start` passes -z to the CLI, so everything that new session does is auto approved; the user's yes to Iris is the only gate, and the prompt still comes only from their own queue.

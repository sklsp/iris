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
- **NEVER words escalate clicks and typing to refusal.** Control or field names matching send/submit/buy/order/pay/delete/password (English) or verzend/betaal/wachtwoord/etc. (Dutch), case-insensitively, are refused with no confirmation path; proven by `test_click_escalates_to_never_when_the_control_says_send_pay_or_password`, `test_type_escalates_to_never_on_password_order_or_payment_fields` and end-to-end by `test_never_tool_by_escalation_is_refused_without_asking` ("Verzenden").
- **The mail/chat Enter rule.** `{ENTER}` anywhere in the key string, when the window matches outlook/gmail/whatsapp/discord/teams/slack, is NEVER; the same keys elsewhere stay confirm; proven by `test_pressing_enter_in_a_mail_or_chat_window_is_never` (10 rows).
- **Trash roots.** Only files under the home folder or `W:/` can be trashed (as confirm); the roots themselves, system dirs, `..` escapes and the trash dir are NEVER; proven by `test_trash_allows_files_under_the_roots_and_refuses_everything_else` and `test_trash_only_takes_allowed_paths`.
- **Untrusted data wrapping.** Every tool result is wrapped in `<untrusted_data>` before it reaches the model; screen text that promises approval still gets the tier the code gives it; proven by `test_screen_text_cannot_change_a_tier`, which replays an injection and asserts both the wrapping and the unchanged tier.
- **Confirm by voice.** CONFIRM tools ask Jay out loud and wait for a yes/no (English or Dutch); a "no" means the function never runs and the call is journaled as declined; proven by `test_declined_confirm_tool_never_runs` and `test_confirmed_confirm_tool_runs_exactly_once`.
- **The unload rule.** Unloading the model Iris is running on right now, or everything at once with "all", escalates `lmstudio_unload` to NEVER and is refused without asking; any other model stays confirm; proven by `test_unload_escalates_to_never_for_all_or_the_running_model` and end-to-end by `test_unloading_the_model_iris_runs_on_is_refused_without_asking`.
- **The GPU check.** `comfy_queue` refuses to post while every GPU is busy or hot, and `lmstudio_load` refuses unless some GPU has 8000 MiB free; both run the same `gpu_guard` before they touch anything; proven by `test_comfy_queue_without_gpu_room_does_not_post`, `test_comfy_queue_without_any_gpu_does_not_post`, `test_load_without_room_returns_the_literal_error` and `test_load_without_any_gpu_returns_the_literal_error`.
- **The journal.** Every tool call lands in `~/.iris/journal.jsonl` with tier, args and outcome (ok/error/declined/refused); proven by `test_every_tool_call_is_journaled`.
- **The prompt text comes only from Jay's own queue.** `cline_start` starts the session with the next prompt from the agent queue file (`IRIS_QUEUE`, a JSON file on disk); nothing Iris reads can put text in it, so screen text cannot choose what gets started; proven by `test_cline_start_starts_the_oldest_prompt_in_the_hub_and_shrinks_the_queue`.
- **Every start is confirmed.** `cline_start` is a confirm tool: Iris asks "Start a new Cline session for <agent> in W:/Iris with the next queued prompt?" (the repo when one is given) and only Jay's yes runs it; a declined answer never starts anything, and if the start fails the queue stays untouched; proven by `test_cline_start_is_a_confirm_tool`, `test_describe_asks_for_cline_start_without_the_prompt_text` and `test_declined_confirm_tool_never_runs`.
- **Window text can never start a run.** An instruction planted in a window to call `cline_start` or `queue_add` is data, not a command; the model must refuse it even when it claims Jay approved it; proven by the cline_injection eval task (safety category).

## Known limits

This run produced no xfail cases: the escalation tables in item 13 matched the real behaviour on first check, so nothing is pinned as a known bug. The limits below are accepted tradeoffs and open gaps:

- **Unknown tool calls are not journaled.** When the model asks for a tool that does not exist it gets an error string, but the call never reaches the journal, so the audit trail has holes (IDEAS.md; pinned as current behaviour by `test_unknown_tool_returns_an_error_and_is_not_journaled`).
- **Over-refusal is accepted.** The never words match word *starts* without a trailing boundary, so harmless names like "Poster" or "Payment" are refused too (documented in `tools.py`; asserted on purpose in `test_click_escalates_to_never_when_the_control_says_send_pay_or_password`/`test_type_escalates_to_never_on_password_order_or_payment_fields`). Safe direction, but Iris sometimes says no to things that were fine.
- **The word lists are fixed English/Dutch regexes.** Other languages or new phrasings do not escalate; "Versenden" does not match the `verzend` stem, and a chat app outside the outlook/gmail/whatsapp/discord/teams/slack list keeps `{ENTER}` at confirm instead of never.
- **The wrapping is advisory to the model.** `<untrusted_data>` tells the model the text is data; it cannot prove the model will not be steered into *other* choices. The hard protection is that tiers and path roots are enforced in code no matter what the model decides.
- **A confirm prompt relies on Jay listening.** The question is spoken once; silence counts as "no", but a misheard or half-heard "yes" approves with no second check.
- **A started session runs in Cline's yolo mode.** `cline_start` passes -z to the CLI, so everything that new session does is auto approved; Jay's yes to Iris is the only gate, and the prompt still comes only from his own queue.

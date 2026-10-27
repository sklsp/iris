# Security notes

What an attacker can do against Iris, what stops it (with the test that proves each stop), and where the protection is known to be thin. Every claim comes from `iris/` or [tests/test_safety.py](tests/test_safety.py).

## Threat model

Iris reads this screen and acts on it, so anything that can put text on the screen can try to steer the agent. An attacker controls:

- **Text on screen** — window contents returned by `read_window`/`read_screen`, including instructions planted in a document or app ("IGNORE ALL RULES...").
- **Window titles** — `list_windows` returns them; a title is just another string that reaches the model.
- **File names** — directory listings and paths read from screen become arguments for file tools like `trash_file`.
- **Web pages read by a tool** — browser content arrives as window text, so a malicious page has the same reach as any other on-screen text.

The attacker does not control the code: tiers, word lists, path roots and the journal live in Python, not in the prompt.

## What stops it

- **Tiers are enforced in code, before the function runs.** `Agent._run` calls `tool.tier_of(args)` on every call; the model's choice of tool or argument cannot change the tier — proven by `test_every_tool_has_a_known_tier` and `test_never_tool_by_base_tier_is_refused_without_asking`.
- **NEVER words escalate clicks and typing to refusal.** Control or field names matching send/submit/buy/order/pay/delete/password (English) or verzend/betaal/wachtwoord/etc. (Dutch), case-insensitively, are refused with no confirmation path — proven by `test_click_escalates_to_never_when_the_control_says_send_pay_or_password`, `test_type_escalates_to_never_on_password_order_or_payment_fields` and end-to-end by `test_never_tool_by_escalation_is_refused_without_asking` ("Verzenden").
- **The mail/chat Enter rule.** `{ENTER}` anywhere in the key string, when the window matches outlook/gmail/whatsapp/discord/teams/slack, is NEVER; the same keys elsewhere stay confirm — proven by `test_pressing_enter_in_a_mail_or_chat_window_is_never` (10 rows).
- **Trash roots.** Only files under the home folder or `W:/` can be trashed (as confirm); the roots themselves, system dirs, `..` escapes and the trash dir are NEVER — proven by `test_trash_allows_files_under_the_roots_and_refuses_everything_else` and `test_trash_only_takes_allowed_paths`.
- **Untrusted data wrapping.** Every tool result is wrapped in `<untrusted_data>` before it reaches the model; screen text that promises approval still gets the tier the code gives it — proven by `test_screen_text_cannot_change_a_tier`, which replays an injection and asserts both the wrapping and the unchanged tier.
- **Confirm by voice.** CONFIRM tools ask Jay out loud and wait for a yes/no (English or Dutch); a "no" means the function never runs and the call is journaled as declined — proven by `test_declined_confirm_tool_never_runs` and `test_confirmed_confirm_tool_runs_exactly_once`.
- **The journal.** Every tool call lands in `~/.iris/journal.jsonl` with tier, args and outcome (ok/error/declined/refused) — proven by `test_every_tool_call_is_journaled`.

## Known limits

This run produced no xfail cases: the escalation tables in item 13 matched the real behaviour on first check, so nothing is pinned as a known bug. The limits below are accepted tradeoffs and open gaps:

- **Unknown tool calls are not journaled.** When the model asks for a tool that does not exist it gets an error string, but the call never reaches the journal, so the audit trail has holes (IDEAS.md; pinned as current behaviour by `test_unknown_tool_returns_an_error_and_is_not_journaled`).
- **Over-refusal is accepted.** The never words match word *starts* without a trailing boundary, so harmless names like "Poster" or "Payment" are refused too (documented in `tools.py`; asserted on purpose in `test_click_escalates_to_never_when_the_control_says_send_pay_or_password`/`test_type_escalates_to_never_on_password_order_or_payment_fields`). Safe direction, but Iris sometimes says no to things that were fine.
- **The word lists are fixed English/Dutch regexes.** Other languages or new phrasings do not escalate — "Versenden" does not match the `verzend` stem, and a chat app outside the outlook/gmail/whatsapp/discord/teams/slack list keeps `{ENTER}` at confirm instead of never.
- **The wrapping is advisory to the model.** `<untrusted_data>` tells the model the text is data; it cannot prove the model will not be steered into *other* choices. The hard protection is that tiers and path roots are enforced in code no matter what the model decides.
- **A confirm prompt relies on Jay listening.** The question is spoken once; silence counts as "no", but a misheard or half-heard "yes" approves with no second check.

# Iris Cline hub run, progress (items 1 to 11)

- [x] C1. the CLI command (543 passed in 19.41s)
- [x] C2. read Cline's session history (549 passed in 20.55s)
- [x] C3. start a session through the hub (558 passed in 22.33s)
- [x] C4. cline_start uses the hub (560 passed in 20.02s)
- [x] C5. delete the VS Code typing (553 passed in 21.13s)
- [x] C6. watch starts in the watched repo (553 passed in 21.22s)
- [x] C7. the tool and the question (553 passed in 20.01s)
- [x] C8. eval fixtures say session (553 passed in 19.31s)
- [x] C9. docs (553 passed in 20.61s)
- [x] C10. no VS Code left, no dashes (553 passed in 20.81s); searched iris/, tests/ (.py plus agents.toml and dashboard/index.html), README.md, SECURITY.md, PLAN.md for "VS Code", "vscode", "paste_into_cline", "cline_keys", "IRIS_VSCODE_TITLE": two hits kept on purpose (tests/test_autopilot.py:323 uses source "vscode" as a non-cline-cli-zen session in test_start_in_hub_ignores_a_session_from_another_source; tests/test_tools.py:52 lists "VS Code" as an example window title for press_keys), no leftovers of the typing code; scanned the 11 files changed on main..HEAD with Python for U+2014/U+2013, none found
- [x] C11. final check (553 passed in 20.41s)

## Cline hub run result
- Commits ahead of main: 11 (including this final check's commit)
- Gates, last lines: ruff "All checks passed!", mypy "Success: no issues found in 11 source files", pytest "553 passed in 20.41s" (coverage floor holds at 96.47%)
- Deleted tests (9): test_cline_keys_escapes_the_command_too, test_cline_keys_is_the_exact_sequence, test_cline_keys_rejects_a_newline, test_cline_start_leaves_the_queue_when_the_paste_fails, test_cline_start_pastes_the_oldest_prompt_and_shrinks_the_queue, test_commands_default_and_read_the_env_when_called, test_paste_into_cline_truncates_the_reported_text, test_paste_into_cline_types_the_exact_sequence, test_paste_into_cline_without_vs_code_raises

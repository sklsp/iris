# Iris 12 progress (eval-language)

- [x] L1. two Dutch phrasings the model misreads (630 passed in 22.76s)
- [x] L2. answer_language (635 passed in 22.15s)
- [x] L3. the task's language (638 passed in 22.56s)
- [x] L4. wrong-language count (641 passed in 22.37s)
- [x] L5. the report lists wrong-language answers (643 passed in 23.05s)
- [x] L6. docs (643 passed in 23.01s)
- [x] L7. no dashes (643 passed in 22.85s)
- [ ] L8. final check

L3 lang changes in eval/tasks.toml: english -> "en", open_app_fails -> "any". Every other request has at least one Dutch word, so it stays nl.

L7 dash scan over the policy-refusals..HEAD files (CHANGELOG.md, EVAL.md, PROGRESS-iris12.md, eval/tasks.toml, iris/eval.py, iris/tools.py, tests/test_eval.py, tests/test_tools.py): one em-dash pair found in EVAL.md (the new Language paragraph), fixed; all other files clean.

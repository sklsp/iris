# Iris 12 progress (eval-language)

- [x] L1. two Dutch phrasings the model misreads (630 passed in 22.76s)
- [x] L2. answer_language (635 passed in 22.15s)
- [x] L3. the task's language (638 passed in 22.56s)
- [x] L4. wrong-language count (641 passed in 22.37s)
- [ ] L5. the report lists wrong-language answers
- [ ] L6. docs
- [ ] L7. no dashes
- [ ] L8. final check

L3 lang changes in eval/tasks.toml: english -> "en", open_app_fails -> "any". Every other request has at least one Dutch word, so it stays nl.

# Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `IRIS_LLM_URL` | `http://127.0.0.1:1234/v1` | LM Studio endpoint |
| `IRIS_MODEL` | `qwen3.8-27b` | model name to request |
| `IRIS_WHISPER` | `large-v3-turbo` on a GPU, `small` on the CPU | faster-whisper model |
| `IRIS_WHISPER_DEVICE` | automatic | `cpu` or `cuda` to force one |
| `IRIS_WHISPER_GPU` | `0` | which GPU Whisper uses, numbered as nvidia-smi shows them |
| `IRIS_WAKEWORD` | `hey_jarvis` | wake word model name, .onnx path, or `iris` to wake on the name through Whisper |
| `IRIS_WAKE_WHISPER` | `tiny` | Whisper size that listens for the name when `IRIS_WAKEWORD=iris` |
| `IRIS_WAKE_THRESHOLD` | `0.5` | wake word score to trigger |
| `IRIS_CONFIRM` | config `confirm`, else `all` | `important` asks only before paying, sending and private files |
| `IRIS_SILENCE` | `1.8` | seconds of silence that end a request |
| `IRIS_FOLLOW_UP` | `8` | seconds after an answer in which a question needs no wake word; 0 turns it off |
| `IRIS_CHIME` | `1` | `0` mutes the beep after the wake word |
| `IRIS_OVERLAY` | config `overlay`, else off | `1` shows your request and the answer on screen in voice mode, `0` hides them |
| `IRIS_VAD_THRESHOLD` | `0.01` | RMS below which audio is silence |
| `IRIS_TTS` | config `tts`, else `sapi` | `voxcpm` for one voice in Dutch and English, `piper`, or `kokoro` |
| `IRIS_VOXCPM_PYTHON` | `~/.iris/voxcpm/venv/Scripts/python.exe` | the Python that has VoxCPM2 |
| `IRIS_VOXCPM_GPU` / `IRIS_VOXCPM_MARGIN` | config `voxcpm_gpu`, else `0` / `1.15` | GPU for VoxCPM2; safety margin on the wait before it plays |
| `IRIS_VOXCPM_STEPS` | `10` | VoxCPM2 diffusion steps (fewer is a little faster, a little rougher) |
| `IRIS_VOXCPM_CACHE` | `512` | VoxCPM2 attention cache positions; smaller is faster, a long answer is spoken in pieces |
| `IRIS_VOXCPM_DIR` | `~/.iris/voxcpm` | where the VoxCPM2 voice pack (its own Python and model) lives |
| `IRIS_VOXCPM_MODEL` | `openbmb/VoxCPM2` | the VoxCPM2 model the worker loads (a Hugging Face id or a folder) |
| `IRIS_STARTUP_DIR` | the Windows Startup folder | where `iris startup on` writes Iris.vbs (tests point it elsewhere) |
| `IRIS_DESKTOP_DIR` | the Windows desktop folder | where `iris shortcut` writes Iris.lnk (tests point it elsewhere) |
| `IRIS_PIPER_DIR` | `~/.iris/piper` | where the Piper voices live |
| `IRIS_PIPER_NL` / `IRIS_PIPER_EN` | `nl_NL-ronnie-medium` / `en_US-joe-medium` | the Piper voice per language |
| `IRIS_KOKORO_DIR` | `~/.iris` | where the Kokoro model files live |
| `IRIS_KOKORO_VOICE` | `af_heart` | Kokoro voice |
| `IRIS_TRASH_DIR` | `~/.iris/trash` | Iris's undoable trash |
| `IRIS_TRASH_ROOTS` | home and config `folders` | roots trash_file may move files from, ';' separated; the home folder is always included |
| `IRIS_AGENTS_TOML` | `~/.iris/agents.toml`, else `iris/agents.toml` | which repos the agent manager watches |
| `IRIS_REPOS` | none | repo paths for the agent manager, ';' separated; used when agents.toml has no repos |
| `IRIS_QUEUE` | `~/.iris/queue.json` | the agent manager's prompt queue |
| `IRIS_CLINE_CLI` | config `cline_cli`, else `cline` | where cline_start finds the Cline CLI that starts sessions in the background hub |
| `IRIS_CLINE_PROVIDER` | `lmstudio` | the provider passed with -P to the Cline CLI |
| `IRIS_CLINE_MODEL` | `qwen3.8-27b` | the model passed with -m to the Cline CLI |
| `IRIS_CLINE_CWD` | config `cline_cwd`, else home | the working directory a Cline session starts in when no repo is given |
| `IRIS_CONCEPTKIT_DIR` | config `conceptkit_dir`, else `~/conceptkit` | where concept_sites runs conceptkit |
| `IRIS_DASHBOARD_PORT` | `8765` | port for the dashboard server, same as `--port` |
| `IRIS_LMS_BIN` | lms on PATH | where the model control finds the LM Studio CLI |
| `IRIS_SESSIONS_DB` | `~/.cline/data/db/sessions.db` | where the Sessions tab reads its rows from |
| `IRIS_CLAUDE_DIR` | `~/.claude/projects` | where the Claude tab looks for active projects |
| `IRIS_COMFY_URL` | `http://127.0.0.1:8188` | where comfy_status looks |
| `IRIS_COMFY_WORKFLOWS` | `~/.iris/workflows` | the folder comfy_workflows lists .json files from |
| `IRIS_LEADSCAN_DIR` | config `leadscan_dir`, else `~/leadscan` | where followups reads out/pipeline.csv from |
| `IRIS_EVAL_DIR` | `eval/results` | what the dashboard's Eval tab reads |
| `IRIS_CONFIG` | `~/.iris/config.toml` | the settings file |
| `IRIS_BROWSER_PROFILE` | Brave, then Chrome, then Edge | the browser user-data folder whose DevToolsActivePort Iris connects to |
| `IRIS_SEARCH_URL` | `https://search.brave.com/search?q={q}` | the results page `web_search` opens in a background tab |
| `IRIS_SKILLS` | `~/.iris/skills` | where self-built skills live |
| `IRIS_JOURNAL` | `~/.iris/journal.jsonl` | the journal the dashboard and autopilot read (`--journal` for Iris itself) |
| `IRIS_NOTES` | `~/.iris/notes.md` | where note_add and notes_read keep notes |
| `IRIS_REMINDERS` | `~/.iris/reminders.json` | pending reminders, kept across restarts |
| `IRIS_SETTINGS` | `~/.iris/settings.json` | the model chosen on the dashboard |
| `IRIS_START_MENU` | the two Windows Start menu folders | where open_app looks for shortcuts, ';' separated |

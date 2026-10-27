# Ideas

- Unknown tool calls (the model asks for a tool name that does not exist) return an error string but are never journaled; consider logging them as event="tool" with outcome="unknown" so every call the model makes is auditable.
- Decide the license when Iris goes public: pyproject already declares MIT metadata, but there is no LICENSE file yet (Jay decides).

"""Which tools Iris offers the model right now: the core tools plus the plugins enabled in config.toml.

Every bundled tool stays in iris.tools.TOOLS (the tier lock and the eval cover all of them); a plugin only
decides whether its tools are offered. Re-evaluated on every request, so enabling a plugin needs no restart.
"""

from __future__ import annotations

from iris import config
from iris.tools import TOOLS, Tool

PLUGINS: dict[str, tuple[str, ...]] = {
    "comfyui": ("comfy_status", "comfy_workflows", "comfy_queue"),
    "lmstudio": ("lmstudio_models", "lmstudio_load", "lmstudio_unload"),
    "leadscan": ("followups",),
    "conceptkit": ("concept_sites",),
    "agents": ("agents_status", "queue_list", "queue_add", "queue_take", "queue_remove", "queue_move", "cline_start"),
}


def active_tools(cfg: dict | None = None) -> dict[str, Tool]:
    """TOOLS without the tools of plugins that config.toml does not enable."""
    enabled = set((cfg or config.load())["plugins"])
    hidden = {name for plugin, names in PLUGINS.items() if plugin not in enabled for name in names}
    return {name: tool for name, tool in TOOLS.items() if name not in hidden}

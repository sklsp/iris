"""Which tools Iris offers the model right now: the core tools, the plugins enabled in config.toml, and the skills
Iris built for herself that the user approved (iris.skills).

Every bundled tool stays in iris.tools.TOOLS (the tier lock and the eval cover all of them); a plugin only
decides whether its tools are offered. Re-evaluated on every request, so enabling a plugin needs no restart.
"""

from __future__ import annotations

from iris import config, skills
from iris.tools import TOOLS, Tool

PLUGINS: dict[str, tuple[str, ...]] = {
    "comfyui": ("comfy_status", "comfy_workflows", "comfy_queue"),
    "lmstudio": ("lmstudio_models", "lmstudio_load", "lmstudio_unload"),
    "leadscan": ("followups",),
    "conceptkit": ("concept_sites",),
    "agents": ("agents_status", "queue_list", "queue_add", "queue_take", "queue_remove", "queue_move", "cline_start"),
}


def active_tools(cfg: dict | None = None) -> dict[str, Tool]:
    """TOOLS without the tools of plugins that config.toml does not enable, plus the approved skills."""
    enabled = set((cfg or config.load())["plugins"])
    hidden = {name for plugin, names in PLUGINS.items() if plugin not in enabled for name in names}
    out = {name: tool for name, tool in TOOLS.items() if name not in hidden}
    for s in skills.approved_tools(set(TOOLS)):
        out[s["name"]] = Tool(s["name"], s["description"] + " (A skill Iris built herself.)", s["params"],
                              s["required"], s["tier"], _skill_fn(s["name"]))
    return out


def _skill_fn(name: str):
    def fn(**kwargs) -> str:
        return skills.run_skill(name, kwargs)
    return fn

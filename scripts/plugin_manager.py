"""Plugin registry and connector status for Locus.

Plugins are JSON manifests under plugins/*/plugin.json. This registry is small
on purpose: it gives the agent a stable capability contract without importing or
running arbitrary plugin code at startup.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.runtime_policy import ROOT

CONFIG_PATH = ROOT / "configs" / "plugins.json"
TOOL_POLICIES = {"default", "allow", "ask", "block"}
AUTONOMY_MODES = {"cautious", "guided", "full_local", "locked"}


def _default_config() -> dict[str, Any]:
    return {
        "enabled_by_default": True,
        "plugin_dirs": ["plugins"],
        "enabled_plugins": [],
        "disabled_plugins": [],
        "connector_policy": {
            "network_checks_on_status": False,
            "require_explicit_send": True,
        },
        "autonomy_mode": "guided",
        "tool_policies": {},
    }


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    version: str
    description: str
    enabled: bool
    path: str
    keywords: list[str]
    capabilities: list[str]
    tools: list[dict[str, Any]]
    connector: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "enabled": self.enabled,
            "path": self.path,
            "keywords": self.keywords,
            "capabilities": self.capabilities,
            "tools": self.tools,
            "connector": self.connector,
            "status": connector_status(self),
        }


def _load_config() -> dict[str, Any]:
    try:
        loaded = json.loads(CONFIG_PATH.read_text())
    except Exception:
        loaded = {}
    config = {**_default_config(), **(loaded if isinstance(loaded, dict) else {})}
    config["enabled_plugins"] = [str(item) for item in config.get("enabled_plugins", [])]
    config["disabled_plugins"] = [str(item) for item in config.get("disabled_plugins", [])]
    if str(config.get("autonomy_mode") or "guided") not in AUTONOMY_MODES:
        config["autonomy_mode"] = "guided"
    policies = config.get("tool_policies", {})
    config["tool_policies"] = {str(key): str(value) for key, value in policies.items()} if isinstance(policies, dict) else {}
    return config


def _write_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


def _is_enabled(plugin_id: str, manifest_enabled: bool) -> bool:
    cfg = _load_config()
    if plugin_id in set(cfg.get("disabled_plugins", [])):
        return False
    if plugin_id in set(cfg.get("enabled_plugins", [])):
        return True
    return manifest_enabled


def _plugin_dirs() -> list[Path]:
    cfg = _load_config()
    dirs = []
    for raw in cfg.get("plugin_dirs", ["plugins"]):
        path = Path(str(raw))
        if not path.is_absolute():
            path = ROOT / path
        dirs.append(path)
    return dirs


def _load_manifest(path: Path) -> Plugin | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    plugin_id = str(data.get("id", "")).strip()
    if not plugin_id:
        return None
    enabled_default = bool(_load_config().get("enabled_by_default", True))
    manifest_enabled = bool(data.get("enabled", enabled_default))
    return Plugin(
        id=plugin_id,
        name=str(data.get("name") or plugin_id),
        version=str(data.get("version") or "0.0.0"),
        description=str(data.get("description") or ""),
        enabled=_is_enabled(plugin_id, manifest_enabled),
        path=str(path.parent),
        keywords=[str(item).lower() for item in data.get("keywords", [])],
        capabilities=[str(item) for item in data.get("capabilities", [])],
        tools=list(data.get("tools", [])),
        connector=data.get("connector") if isinstance(data.get("connector"), dict) else None,
    )


def _manifest_enabled_default(plugin_id: str) -> bool:
    enabled_default = bool(_load_config().get("enabled_by_default", True))
    for directory in _plugin_dirs():
        manifest = directory / plugin_id / "plugin.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text())
        except Exception:
            return enabled_default
        return bool(data.get("enabled", enabled_default))
    return enabled_default


def list_plugins(enabled_only: bool = False) -> list[Plugin]:
    plugins: list[Plugin] = []
    for directory in _plugin_dirs():
        if not directory.exists():
            continue
        for manifest in sorted(directory.glob("*/plugin.json")):
            plugin = _load_manifest(manifest)
            if plugin and (plugin.enabled or not enabled_only):
                plugins.append(plugin)
    return plugins


def get_plugin(plugin_id: str) -> Plugin | None:
    for plugin in list_plugins(enabled_only=False):
        if plugin.id == plugin_id:
            return plugin
    return None


def set_plugin_enabled(plugin_id: str, enabled: bool) -> dict[str, Any]:
    plugin = get_plugin(plugin_id)
    if plugin is None:
        raise ValueError(f"unknown plugin: {plugin_id}")
    cfg = _load_config()
    enabled_plugins = set(cfg.get("enabled_plugins", []))
    disabled_plugins = set(cfg.get("disabled_plugins", []))
    if enabled:
        disabled_plugins.discard(plugin_id)
        if _manifest_enabled_default(plugin_id):
            enabled_plugins.discard(plugin_id)
        else:
            enabled_plugins.add(plugin_id)
    else:
        enabled_plugins.discard(plugin_id)
        disabled_plugins.add(plugin_id)
    cfg["enabled_plugins"] = sorted(enabled_plugins)
    cfg["disabled_plugins"] = sorted(disabled_plugins)
    _write_config(cfg)
    return registry_snapshot()


def tool_policy(plugin_id: str, tool_name: str, risk: str | None = None) -> str:
    cfg = _load_config()
    key = f"{plugin_id}.{tool_name}"
    override = str(cfg.get("tool_policies", {}).get(key, "default"))
    if override in TOOL_POLICIES and override != "default":
        return override
    risk = str(risk or "read")
    mode = str(cfg.get("autonomy_mode") or "guided")
    if mode == "cautious":
        return "ask"
    if mode == "full_local":
        return "ask" if risk in {"network", "external_write", "unknown"} else "allow"
    if mode == "locked":
        return "allow" if risk == "read" else "block"
    return "allow" if risk == "read" else "ask"


def set_tool_policy(plugin_id: str, tool_name: str, policy: str) -> dict[str, Any]:
    policy = str(policy or "default").strip().lower()
    if policy not in TOOL_POLICIES:
        raise ValueError(f"invalid tool policy: {policy}")
    plugin = get_plugin(plugin_id)
    if plugin is None:
        raise ValueError(f"unknown plugin: {plugin_id}")
    if not any(str(tool.get("name") or "") == tool_name for tool in plugin.tools):
        raise ValueError(f"unknown tool: {plugin_id}.{tool_name}")

    cfg = _load_config()
    policies = dict(cfg.get("tool_policies", {}))
    key = f"{plugin_id}.{tool_name}"
    if policy == "default":
        policies.pop(key, None)
    else:
        policies[key] = policy
    cfg["tool_policies"] = dict(sorted(policies.items()))
    _write_config(cfg)
    return registry_snapshot()


def set_autonomy_mode(mode: str) -> dict[str, Any]:
    mode = str(mode or "guided").strip().lower()
    if mode not in AUTONOMY_MODES:
        raise ValueError(f"invalid autonomy mode: {mode}")
    cfg = _load_config()
    cfg["autonomy_mode"] = mode
    _write_config(cfg)
    return registry_snapshot()


def autonomy_mode_detail(mode: str | None = None) -> str:
    labels = {
        "cautious": "asks before every implemented tool",
        "guided": "allows read-only tools and asks before write, shell, network, and external actions",
        "full_local": "allows local reads, writes, and shell commands; asks before network and external actions",
        "locked": "allows read-only tools and blocks write, shell, network, and external actions",
    }
    return labels.get(str(mode or _load_config().get("autonomy_mode") or "guided"), labels["guided"])


def plugins_for_goal(goal: str) -> list[Plugin]:
    normalized = (goal or "").lower()
    scored: list[tuple[int, Plugin]] = []
    for plugin in list_plugins(enabled_only=True):
        score = sum(1 for keyword in plugin.keywords if keyword and keyword in normalized)
        if score:
            scored.append((score, plugin))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [plugin for _, plugin in scored]


def connector_status(plugin: Plugin) -> dict[str, Any]:
    if not plugin.connector:
        return {"kind": "local", "configured": True, "detail": "local capability"}

    connector = plugin.connector
    env_names = [str(name) for name in connector.get("env", [])]
    present_env = [name for name in env_names if os.getenv(name)]
    cli = connector.get("cli")
    cli_path = shutil.which(str(cli)) if cli else None

    browser_urls = [str(url) for url in connector.get("browser_urls", [])]
    configured = bool(present_env or cli_path)
    detail_parts: list[str] = []
    if present_env:
        detail_parts.append("env configured: " + ", ".join(present_env))
    if cli_path:
        detail_parts.append(f"cli available: {cli}")
    if browser_urls:
        detail_parts.append("browser workflow available after explicit approval")
    if not detail_parts:
        detail_parts.append("credentials not configured")

    return {
        "kind": connector.get("type", "connector"),
        "configured": configured,
        "env_present": present_env,
        "cli_available": bool(cli_path),
        "browser_workflow_available": bool(browser_urls),
        "detail": "; ".join(detail_parts),
    }


def registry_snapshot() -> dict[str, Any]:
    plugins = [plugin.to_dict() for plugin in list_plugins(enabled_only=False)]
    enabled = [plugin for plugin in plugins if plugin.get("enabled")]
    return {
        "plugins": plugins,
        "enabled_count": len(enabled),
        "total_count": len(plugins),
        "config": _load_config(),
    }


def render_plugin_context(goal: str) -> str:
    matches = plugins_for_goal(goal)
    if not matches:
        matches = list_plugins(enabled_only=True)
    lines = ["Available plugins:"]
    for plugin in matches:
        tools = ", ".join(tool.get("name", "") for tool in plugin.tools)
        status = connector_status(plugin)
        lines.append(f"- {plugin.id}: {plugin.description} Tools: {tools}. Status: {status['detail']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Locus plugins")
    parser.add_argument("--json", action="store_true", help="Print registry JSON")
    parser.add_argument("--goal", help="Show plugins matching a goal")
    args = parser.parse_args()

    if args.goal:
        matches = [plugin.to_dict() for plugin in plugins_for_goal(args.goal)]
        print(json.dumps({"matches": matches}, indent=2) if args.json else render_plugin_context(args.goal))
        return

    snapshot = registry_snapshot()
    if args.json:
        print(json.dumps(snapshot, indent=2))
        return
    print(f"Plugins: {snapshot['enabled_count']} enabled / {snapshot['total_count']} installed")
    for plugin in snapshot["plugins"]:
        marker = "on " if plugin["enabled"] else "off"
        print(f"  {marker} {plugin['id']:12s} {plugin['description']}")


if __name__ == "__main__":
    main()

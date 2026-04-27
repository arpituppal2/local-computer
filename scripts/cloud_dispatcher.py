"""Cloud offload wrapper for subagent dispatch with local Ollama tunnel."""
from __future__ import annotations
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
_MODELS_PATH = ROOT / "configs" / "models.json"
_MODELS = json.loads(_MODELS_PATH.read_text()) if _MODELS_PATH.exists() else {}

MODEL_PLANNER = _MODELS.get("planner", "qwen3:8b")
MODEL_HEAVY  = _MODELS.get("heavy", "qwen3:14b")

# Free cloud platforms for hosting stateless subagent workers
CLOUD_PLATFORMS = {
    "cloud_run":  {"cli": "gcloud", "check": "gcloud config get-value project", "deploy": "gcloud run deploy"},
    "railway":   {"cli": "railway", "check": "railway whoami", "deploy": "railway up"},
    "render":    {"cli": "render", "check": "render services list", "deploy": "render services deploy"},
    "huggingface": {"cli": "huggingface-cli", "check": "huggingface-cli whoami", "deploy": "huggingface-cli upload"},
}

# Ollama tunnel for remote inference back to local MacBook
# Use ngrok/Cloudflare Tunnel to expose Ollama API to cloud workers
OLLAMA_TUNNEL_CMD = "cloudflared tunnel --url http://localhost:11434"


def get_ollama_tunnel_url() -> Optional[str]:
    """Get the tunnel URL if Ollama is exposed via cloudflared/ngrok."""
    tunnel_env = os.environ.get("OLLAMA_TUNNEL_URL")
    if tunnel_env:
        return tunnel_env
    # Try to detect an active tunnel
    try:
        result = subprocess.run(
            ["pgrep", "-f", "cloudflared"],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            logging.info("[dispatcher] cloudflared tunnel detected")
            return tunnel_env or "http://localhost:11434"
    except Exception:
        pass
    return None


def deploy_worker(platform: str, worker_path: str) -> Dict[str, Any]:
    """Deploy a stateless subagent worker to a cloud platform."""
    if platform not in CLOUD_PLATFORMS:
        return {"success": False, "error": f"Unknown platform: {platform}"}

    config = CLOUD_PLATFORMS[platform]
    try:
        # Check CLI availability
        check = subprocess.run(config["check"].split(), capture_output=True, timeout=5)
        if check.returncode != 0:
            return {"success": False, "error": f"{platform} CLI not available"}

        # Deploy
        deploy_cmd = f"{config['deploy']} {worker_path}"
        result = subprocess.run(deploy_cmd, shell=True, capture_output=True, timeout=60)
        if result.returncode == 0:
            logging.info(f"[dispatcher] Deployed worker to {platform}")
            return {"success": True, "platform": platform, "output": result.stdout.decode()}
        else:
            return {"success": False, "error": result.stderr.decode()}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Deployment timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def dispatch_to_cloud(
    task: Dict[str, Any],
    platform: str = "cloud_run",
    worker_url: Optional[str] = None,
    local_fallback: bool = True
) -> Dict[str, Any]:
    """Dispatch a subagent task to a cloud worker, with local fallback."""
    if not worker_url:
        # Check if we have a tunnel
        tunnel_url = get_ollama_tunnel_url()
        if tunnel_url:
            # Cloud worker would call back to local Ollama via tunnel
            logging.info(f"[dispatcher] Using Ollama tunnel: {tunnel_url}")
        # For now, no remote worker URL means local fallback
        if local_fallback:
            logging.warning("[dispatcher] No cloud worker URL; falling back to local")
            return dispatch_local(task)
        return {"success": False, "error": "No cloud worker URL provided"}

    try:
        import httpx
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(worker_url, json=task)
            resp.raise_for_status()
            result = resp.json()
            logging.info(f"[dispatcher] Cloud result from {platform}")
            return result
    except Exception as e:
        logging.warning(f"[dispatcher] Cloud dispatch failed: {e}")
        if local_fallback:
            return dispatch_local(task)
        return {"success": False, "error": str(e)}


def dispatch_local(task: Dict[str, Any]) -> Dict[str, Any]:
    """Run a subagent task locally via Ollama."""
    goal = task.get("goal", "")
    prompt = f"Complete this research task and return JSON with 'findings' key:\n{goal}"
    try:
        from scripts.ollama_client import call_json
        result = call_json(MODEL_PLANNER, prompt)
        return {"status": "done", "goal": goal, "output": result, "source": "local"}
    except Exception as e:
        return {"status": "error", "goal": goal, "error": str(e), "source": "local"}


def start_ollama_tunnel() -> Optional[subprocess.Popen]:
    """Start a cloudflared tunnel to expose local Ollama to the internet."""
    try:
        proc = subprocess.Popen(
            OLLAMA_TUNNEL_CMD.split(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        time.sleep(3)  # Wait for tunnel to establish
        logging.info("[dispatcher] Started Ollama tunnel")
        return proc
    except Exception as e:
        logging.error(f"[dispatcher] Failed to start tunnel: {e}")
        return None


def stop_ollama_tunnel(proc: Optional[subprocess.Popen]) -> None:
    """Stop the Ollama tunnel process."""
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        logging.info("[dispatcher] Stopped Ollama tunnel")

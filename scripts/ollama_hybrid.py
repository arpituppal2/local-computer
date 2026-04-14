"""Routes Ollama prompts to CPU or GPU based on estimated token count."""
import requests

API_URL         = "http://127.0.0.1:11434/api/generate"
TOKEN_THRESHOLD = 20


def estimate_tokens(prompt: str) -> int:
    return max(1, int(len(prompt.split()) / 0.75))


def send_prompt(prompt: str, model: str = "qwen3:4b") -> str:
    tokens = estimate_tokens(prompt)
    device = "cpu" if tokens < TOKEN_THRESHOLD else "gpu"
    payload = {
        "model":      model,
        "prompt":     prompt,
        "stream":     False,
        "options":    {"device": device},
        "max_tokens": 512,
    }
    response = requests.post(API_URL, json=payload)
    response.raise_for_status()
    return response.json().get("response", "")

"""One call shape for every LLM provider.

Every agent reads its entry from config.json:

    "summary": {"base_url": "...", "key_name": "GEMINI_API_KEY", "model": "..."}

Any OpenAI-compatible chat endpoint works: Gemini (OpenAI-compatible URL),
OpenRouter, OpenAI, DeepSeek, Groq. Keys come from environment variables
(GitHub Secrets in Actions), never from files.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class LLMError(RuntimeError):
    pass


def chat(agent_cfg: dict, system: str, user: str, temperature: float = 0.0, timeout: int = 120) -> dict:
    """Return {"text": ..., "model": "<base host>/<model>"}."""
    key = os.environ.get(agent_cfg["key_name"], "")
    if not key:
        raise LLMError(f"Secret {agent_cfg['key_name']} is not set")
    url = agent_cfg["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": agent_cfg["model"],
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise LLMError(f"{url} returned {e.code}: {e.read()[:300]!r}") from e
    text = data["choices"][0]["message"]["content"]
    host = urllib.parse.urlparse(agent_cfg["base_url"]).hostname or "llm"
    return {"text": text, "model": f"{host}/{agent_cfg['model']}"}

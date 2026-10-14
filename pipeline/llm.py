"""One call shape for every LLM provider.

Every agent reads its entry from config.json:

    "summary": {"base_url": "...", "key_name": "GEMINI_API_KEY", "model": "...", "min_interval": 6}

Any OpenAI-compatible chat endpoint works: Gemini (OpenAI-compatible URL),
OpenRouter, OpenAI, DeepSeek, Groq. Keys come from environment variables
(GitHub Secrets in Actions), never from files.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

_last_call = {}


class LLMError(RuntimeError):
    pass


def _key(agent_cfg):
    key = os.environ.get(agent_cfg["key_name"], "")
    if not key:
        raise LLMError(f"Secret {agent_cfg['key_name']} is not set")
    return key


def list_models(agent_cfg) -> list:
    """Model ids the endpoint offers (used to print a hint when the model name is wrong)."""
    url = agent_cfg["base_url"].rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_key(agent_cfg)}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception:
        return []
    return sorted(m.get("id", "") for m in data.get("data", []))


def chat(agent_cfg: dict, system: str, user: str, temperature: float = 0.0, timeout: int = 180, retries: int = 3) -> dict:
    """Return {"text": ..., "model": "<base host>/<model>"}."""
    model = agent_cfg.get("model", "")
    if not model or model == "SET-ME":
        models = list_models(agent_cfg)
        hint = f" Available: {', '.join(models[:40])}" if models else ""
        raise LLMError(f"Set a model name in config.json (llm.*.model).{hint}")
    key = _key(agent_cfg)

    # simple pacing for free tiers (requests per minute limits)
    gap = float(agent_cfg.get("min_interval", 0))
    host = urllib.parse.urlparse(agent_cfg["base_url"]).hostname or "llm"
    wait = _last_call.get(host, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)

    url = agent_cfg["base_url"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            detail = e.read()[:400]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(20 * (attempt + 1))
                continue
            if e.code == 404:
                models = list_models(agent_cfg)
                hint = f" Available: {', '.join(models[:40])}" if models else ""
                raise LLMError(f"Model '{model}' not found at {host}.{hint}") from e
            raise LLMError(f"{url} returned {e.code}: {detail!r}") from e
    _last_call[host] = time.time()
    text = data["choices"][0]["message"]["content"] or ""
    return {"text": text, "model": f"{host}/{model}"}


def parse_json(text: str):
    """Pull the first JSON object or array out of a model reply (tolerates ```json fences and prose)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise LLMError(f"No JSON in reply: {text[:200]!r}")
    start = min(starts)
    closer = "}" if t[start] == "{" else "]"
    end = t.rfind(closer)
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"Bad JSON in reply: {e}: {text[:200]!r}") from e

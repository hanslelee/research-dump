"""One call shape for every LLM provider.

Every agent reads its entry from config.json:

    "summary": {"base_url": "...", "key_name": "GEMINI_API_KEY", "model": "...",
                "fallback_model": "...", "min_interval": 6}

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
RETRY_WAITS = (15, 30, 60)


class LLMError(RuntimeError):
    """The reply could not be used (bad JSON, bad request). Skip this item and go on."""


class LLMUnavailable(LLMError):
    """The service is down, overloaded or out of quota. Stop this stage and try next run."""


def _key(agent_cfg):
    key = os.environ.get(agent_cfg["key_name"], "")
    if not key:
        raise LLMUnavailable(f"Secret {agent_cfg['key_name']} is not set")
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


def _post(agent_cfg, model, system, user, temperature, json_mode, timeout):
    key = _key(agent_cfg)
    gap = float(agent_cfg.get("min_interval", 0))
    host = urllib.parse.urlparse(agent_cfg["base_url"]).hostname or "llm"
    wait = _last_call.get(host, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    body = {"model": model, "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(agent_cfg["base_url"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    finally:
        _last_call[host] = time.time()
    return data["choices"][0]["message"]["content"] or "", f"{host}/{model}"


def chat(agent_cfg: dict, system: str, user: str, temperature: float = 0.0, timeout: int = 180,
         json_mode: bool = False) -> dict:
    """Return {"text": ..., "model": "<host>/<model actually used>"}.

    Busy or rate-limited (429/5xx): wait and retry, then try fallback_model once.
    """
    model = agent_cfg.get("model", "")
    if not model or model == "SET-ME":
        models = list_models(agent_cfg)
        hint = f" Available: {', '.join(models[:40])}" if models else ""
        raise LLMUnavailable(f"Set a model name in config.json (llm.*.model).{hint}")

    models = [model] + ([agent_cfg["fallback_model"]] if agent_cfg.get("fallback_model") else [])
    last = None
    for m in models:
        for attempt, wait in enumerate((0,) + RETRY_WAITS):
            if wait:
                time.sleep(wait)
            try:
                text, used = _post(agent_cfg, m, system, user, temperature, json_mode, timeout)
                return {"text": text, "model": used}
            except urllib.error.HTTPError as e:
                detail = e.read()[:300]
                if e.code == 400 and json_mode:
                    json_mode = False  # endpoint does not support response_format: retry without it
                    continue
                if e.code in (429, 500, 502, 503, 504):
                    last = f"{m} returned {e.code}: {detail!r}"
                    continue
                if e.code == 404:
                    avail = list_models(agent_cfg)
                    hint = f" Available: {', '.join(avail[:40])}" if avail else ""
                    raise LLMUnavailable(f"Model '{m}' not found.{hint}") from e
                raise LLMUnavailable(f"{m} returned {e.code}: {detail!r}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last = f"{m}: {e}"
                continue
    raise LLMUnavailable(f"busy or out of quota after retries ({last})")


def parse_json(text: str):
    """Pull the first JSON object or array out of a model reply.

    Tolerates ```json fences, prose around the JSON and trailing commas.
    """
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    candidates = [t]
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if starts:
        start = min(starts)
        closer = "}" if t[start] == "{" else "]"
        candidates.append(t[start:t.rfind(closer) + 1])
    for c in candidates:
        for variant in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                return json.loads(variant)
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Bad JSON in reply: {text[:200]!r}")

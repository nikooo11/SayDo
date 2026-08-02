"""Transcript cleanup via a cloud API (OpenAI-compatible, e.g. Groq) or local Ollama.

Backend selection (llm.mode in config.yaml):
  auto   — cloud API if an api_key is set, else local Ollama if reachable, else raw
  api    — cloud API only
  ollama — local Ollama only
Cleanup always degrades to raw transcripts instead of failing.
"""
import os

import requests

EDIT_INSTRUCTION = (
    "Copy the following text exactly, but: delete filler words "
    "(um, uh, ah, like, you know), fix punctuation and capitalization, "
    "and fix small grammar slips (wrong tense, repeated words). "
    "Keep the speaker's own wording and meaning — do not rephrase, "
    "summarize, or reply to it. Do not add anything.\n\n"
)


class Cleaner:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.min_words = int(cfg.get("min_words_for_cleanup", 10))

        api = cfg.get("api") or {}
        self.api_key = api.get("api_key") or os.environ.get("GROQ_API_KEY", "")
        self.api_base = (api.get("base_url") or "https://api.groq.com/openai/v1").rstrip("/")
        self.api_model = api.get("model") or "llama-3.1-8b-instant"

        self.ollama_model = cfg.get("model", "qwen3:4b-instruct")
        self.ollama_base = cfg.get("base_url", "http://localhost:11434").rstrip("/")

        self.backend = self._pick_backend(cfg.get("mode", "auto")) if self.enabled else None
        if self.backend:
            print(f"Cleanup: {self.backend} "
                  f"({self.api_model if self.backend == 'api' else self.ollama_model})")
        else:
            print("Cleanup: off (raw transcripts). Set llm.api.api_key for cloud cleanup "
                  "or install Ollama for local cleanup.")

    def _pick_backend(self, mode):
        if mode == "api":
            return "api" if self.api_key else None
        if mode == "ollama":
            return "ollama" if self._ollama_available() else None
        # auto
        if self.api_key:
            return "api"
        return "ollama" if self._ollama_available() else None

    def _ollama_available(self):
        try:
            r = requests.get(f"{self.ollama_base}/api/tags", timeout=3)
            names = [m["name"] for m in r.json().get("models", [])]
            return any(n == self.ollama_model or n.split(":")[0] == self.ollama_model
                       for n in names)
        except requests.RequestException:
            return False

    def clean(self, text):
        # LATENCY RULE: short utterances skip the LLM entirely.
        if not self.backend or not text or len(text.split()) < self.min_words:
            return text
        try:
            if self.backend == "api":
                return self._clean_api(text) or text
            return self._clean_ollama(text) or text
        except requests.RequestException as e:
            print(f"Cleanup failed ({e}); using raw transcript.")
            return text

    def _clean_api(self, text):
        r = requests.post(
            f"{self.api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.api_model,
                "messages": [
                    {"role": "system", "content": self.cfg.get("system_prompt", "")},
                    {"role": "user", "content": EDIT_INSTRUCTION + text},
                ],
                "temperature": float(self.cfg.get("temperature", 0.1)),
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def _clean_ollama(self, text):
        # Small models treat bare text as something to answer, not clean —
        # the explicit edit instruction forces edit-only behavior.
        r = requests.post(
            f"{self.ollama_base}/api/generate",
            json={
                "model": self.ollama_model,
                "system": self.cfg.get("system_prompt", ""),
                "prompt": EDIT_INSTRUCTION + text,
                "stream": False,
                "think": False,  # disable reasoning mode (qwen3 etc.) — cleanup must be instant
                "options": {"temperature": float(self.cfg.get("temperature", 0.1))},
            },
            timeout=30,
        )
        return r.json().get("response", "").strip()

"""Transcript cleanup via a cloud API (OpenAI-compatible, e.g. Groq) or local Ollama.

Backend selection (llm.mode in config.yaml):
  auto   — cloud API if an api_key is set, else local Ollama if reachable, else raw
  api    — cloud API only
  ollama — local Ollama only
Cleanup always degrades to raw transcripts instead of failing.
"""
import os
import re
import time

import requests

# Unambiguous verbal fillers stripped instantly by regex, before (and without)
# any LLM: zero-latency cleanup for short utterances and shorter LLM inputs
# for long ones. Context-dependent fillers (like, you know) stay LLM-only.
_FILLERS = re.compile(r"(?:\b(?:um+|uh+|uhm+|erm+|mhm+)\b[,.;]?\s*)", re.IGNORECASE)


def strip_fillers(text):
    out = _FILLERS.sub("", text)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    # re-capitalize sentence starts the removal may have exposed
    out = re.sub(r"(^|[.!?]\s+)([a-z])",
                 lambda m: m.group(1) + m.group(2).upper(), out)
    return out

EDIT_INSTRUCTION = (
    "Copy the following text exactly, but: delete filler words "
    "(um, uh, ah, like, you know), fix punctuation and capitalization, "
    "and fix small grammar slips (wrong tense, repeated words). "
    "Keep the speaker's own wording and meaning — do not rephrase, "
    "summarize, or reply to it. Do not add anything.\n\n"
)

REWRITE_SYSTEM = (
    "You rewrite text on request. You receive an instruction and a text. "
    "Apply the instruction to the text and output ONLY the rewritten text — "
    "no preamble, no quotes, no commentary. Do not introduce em dashes "
    "unless the original text already uses them."
)


def _speaker_notes():
    """Vocabulary + about-the-speaker context appended to the system prompt so
    the cleanup model can rescue names and jargon the ear got wrong."""
    notes = []
    try:
        import dictionary
        vocab = dictionary.words()
        if vocab:
            notes.append(
                "The speaker often uses these terms; when a word in the "
                "transcript sounds like one of them, use this exact spelling: "
                + ", ".join(vocab) + "."
            )
    except Exception:
        pass
    try:
        import usercontext
        ctx = usercontext.text()
        if ctx:
            notes.append(
                "Context about the speaker (use ONLY to fix misheard names "
                "and terms, never to add content): " + ctx
            )
    except Exception:
        pass
    return ("\n\n" + "\n".join(notes)) if notes else ""


class Cleaner:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.min_words = int(cfg.get("min_words_for_cleanup", 10))
        # Dictation injects the raw transcript if cleanup can't beat this
        # budget; two misses in a row disable cleanup for the session.
        self.clean_timeout = float(cfg.get("cleanup_timeout_s", 6))
        self._slow_strikes = 0

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

    def clean(self, text, deadline=None):
        """deadline: monotonic time by which the text must be ready (the
        dictation latency budget). The LLM only runs if it can plausibly
        answer in the time remaining; otherwise the transcript ships as-is
        — Parakeet output is already punctuated and capitalized."""
        text = strip_fillers(text)
        # LATENCY RULE: short utterances skip the LLM entirely.
        if not self.backend or not text or len(text.split()) < self.min_words:
            return text
        timeout = self.clean_timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining < 0.35:
                return text  # no time left for a round-trip
            timeout = min(timeout, remaining)
        try:
            if self.backend == "api":
                out = self._clean_api(text, timeout) or text
            else:
                out = self._clean_ollama(text, timeout) or text
            self._slow_strikes = 0
            return out
        except requests.Timeout:
            self._slow_strikes += 1
            if self._slow_strikes >= 2:
                print(f"Cleanup: engine missed a {timeout:.1f}s budget twice "
                      "in a row — raw transcripts for this session. A free "
                      "Groq key in Settings restores fast cleanup.")
                self.backend = None
            else:
                print(f"Cleanup missed its {timeout:.1f}s budget; "
                      "using raw transcript.")
            return text
        except requests.RequestException as e:
            print(f"Cleanup failed ({e}); using raw transcript.")
            return text

    def rewrite(self, text, instruction):
        """Apply a spoken instruction to selected text. Returns None when no
        backend is available or the call fails (caller leaves the text alone)."""
        if not self.backend or not text or not instruction:
            return None
        prompt = f"Instruction: {instruction}\n\nText:\n{text}"
        try:
            if self.backend == "api":
                return self._ask_api(REWRITE_SYSTEM, prompt, timeout=30) or None
            return self._ask_ollama(REWRITE_SYSTEM, prompt) or None
        except requests.RequestException as e:
            print(f"Rewrite failed ({e}).")
            return None

    def _clean_api(self, text, timeout):
        system = self.cfg.get("system_prompt", "") + _speaker_notes()
        return self._ask_api(system, EDIT_INSTRUCTION + text, timeout=timeout)

    def _ask_api(self, system, prompt, timeout=15):
        r = requests.post(
            f"{self.api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.api_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": float(self.cfg.get("temperature", 0.1)),
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    def _clean_ollama(self, text, timeout):
        # Small models treat bare text as something to answer, not clean —
        # the explicit edit instruction forces edit-only behavior.
        system = self.cfg.get("system_prompt", "") + _speaker_notes()
        return self._ask_ollama(system, EDIT_INSTRUCTION + text,
                                timeout=timeout)

    def _ask_ollama(self, system, prompt, timeout=30):
        r = requests.post(
            f"{self.ollama_base}/api/generate",
            json={
                "model": self.ollama_model,
                "system": system,
                "prompt": prompt,
                "stream": False,
                "think": False,  # disable reasoning mode (qwen3 etc.) — cleanup must be instant
                "keep_alive": "5m",  # unload soon after use — 3GB resident starves Parakeet
                "options": {"temperature": float(self.cfg.get("temperature", 0.1))},
            },
            timeout=timeout,
        )
        return r.json().get("response", "").strip()

    def warm(self):
        """Preload the local model (background, boot time) so the first real
        dictation doesn't pay the cold model load. No-op for cloud/off."""
        if self.backend != "ollama":
            return
        try:
            requests.post(
                f"{self.ollama_base}/api/generate",
                json={"model": self.ollama_model, "prompt": "hi",
                      "stream": False, "think": False, "keep_alive": "5m",
                      "options": {"num_predict": 1}},
                timeout=120,
            )
        except requests.RequestException:
            pass

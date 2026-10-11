"""Independent translation engines; none uses the ChatGPT request runner.

Cloud credentials travel in HTTPS headers, never URLs or log messages. Offline
models are loaded with local_files_only; only download_offline() uses the network.
"""

from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .paths import MODELS_DIR

ENGINES = {"azure": "Azure Translator", "google": "Google Cloud Translation", "google_web": "Google Translate (Chrome)", "gemini": "Gemini API", "vinai": "VinAI offline"}
LANGUAGES = {"en": "English", "vi": "Vietnamese", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "fr": "French", "de": "German", "es": "Spanish"}
OFFLINE_REPO = "vinai/vinai-translate-en2vi-v2"
OFFLINE_DIR = MODELS_DIR / "translation" / "vinai-en2vi-v2"


class TranslationError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    engine: str = "azure"
    source: str = "en"
    target: str = "vi"
    key: str = ""
    region: str = ""
    model: str = ""
    device: str = "cpu"
    prompt: str = ""

    @classmethod
    def from_settings(cls, settings):
        engine = settings.translation_engine
        return cls(engine, settings.translation_source, settings.translation_target,
                   getattr(settings, f"translation_{engine}_key", ""),
                   settings.translation_azure_region if engine == "azure" else "",
                   settings.translation_gemini_model if engine == "gemini" else (OFFLINE_REPO if engine == "vinai" else ("web" if engine == "google_web" else "nmt")),
                   settings.translation_device,
                   settings.translation_gemini_prompt if engine == "gemini" else "")

    @property
    def fingerprint(self):
        # Credentials are deliberately excluded from stored metadata/cache keys.
        value = (self.engine, self.source, self.target, self.model, self.prompt)
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()

    def validate(self):
        if self.engine not in ENGINES:
            raise TranslationError("Choose a supported translation engine.")
        if self.source not in LANGUAGES or self.target not in LANGUAGES or self.source == self.target:
            raise TranslationError("Choose different source and target languages.")
        if self.engine in ("azure", "google", "gemini") and not self.key.strip():
            raise TranslationError("Enter the API key for the selected engine in Settings.")
        if self.engine == "gemini":
            if not re.fullmatch(r"[\w.-]+", self.model):
                raise TranslationError("Enter a valid Gemini model ID.")
            if "{text}" not in self.prompt:
                raise TranslationError("The Gemini prompt must contain {text}.")
        if self.engine == "vinai":
            if (self.source, self.target) != ("en", "vi"):
                raise TranslationError("VinAI offline currently supports English → Vietnamese only.")
            if self.device not in ("cpu", "cuda", "auto"):
                raise TranslationError("Choose CPU, CUDA or auto for VinAI.")


def _post(url, body, headers):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json; charset=UTF-8", **headers})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        messages = {401: "Invalid API key.", 403: "Access denied; check API enablement, billing and quota.",
                    429: "Rate limit or quota reached. Wait, then retry manually."}
        raise TranslationError(f"HTTP {exc.code}: " + messages.get(exc.code, "Translation request failed. Check engine configuration.")) from None
    except (OSError, ValueError):
        raise TranslationError("Could not reach the translation service or read its response. Check the network and retry.") from None


def _chunks(text, maximum=1200):
    while len(text) > maximum:
        end = text.rfind(" ", 0, maximum)
        if end < maximum // 2:
            end = maximum
        yield text[:end]
        text = text[end:].lstrip()
    if text:
        yield text


class Translator:
    def __init__(self):
        self._offline = None
        self._offline_device = None
        self._lock = threading.RLock()

    def translate(self, text, config: Config, cancel=None):
        config.validate()
        if config.engine == "google_web":
            from .google_translate import translate
            return translate(text.strip(), config.source, config.target, cancel)
        output = []
        for chunk in _chunks(text.strip()):
            if cancel and cancel.is_set():
                raise TranslationError("Translation cancelled.")
            try:
                output.append(self._one(chunk, config).strip())
            except (KeyError, IndexError, TypeError, AttributeError):
                raise TranslationError("The service returned an unexpected or empty response.") from None
        if not output or not all(output):
            raise TranslationError("The service returned an empty translation.")
        return " ".join(output)

    def _one(self, text, config):
        if config.engine == "azure":
            codes = {"zh": "zh-Hans"}
            query = urllib.parse.urlencode({"api-version": "3.0", "from": codes.get(config.source, config.source), "to": codes.get(config.target, config.target)})
            headers = {"Ocp-Apim-Subscription-Key": config.key}
            if config.region:
                headers["Ocp-Apim-Subscription-Region"] = config.region
            return _post("https://api.cognitive.microsofttranslator.com/translate?" + query,
                         [{"Text": text}], headers)[0]["translations"][0]["text"]
        if config.engine == "google":
            body = {"q": text, "source": config.source, "target": config.target, "format": "text", "model": "nmt"}
            data = _post("https://translation.googleapis.com/language/translate/v2", body, {"X-Goog-Api-Key": config.key})
            return html.unescape(data["data"]["translations"][0]["translatedText"])
        if config.engine == "gemini":
            prompt = config.prompt.replace("{source}", LANGUAGES[config.source]).replace("{target}", LANGUAGES[config.target]).replace("{text}", text)
            data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{config.model}:generateContent",
                         {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 0, "maxOutputTokens": 2048}}, {"x-goog-api-key": config.key})
            candidate = data["candidates"][0]
            if candidate.get("finishReason") not in (None, "STOP"):
                raise TranslationError("Gemini did not complete the translation (blocked or output limit). Retry or change the model.")
            return "".join(p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought"))
        with self._lock:
            return self._translate_offline(text, config)

    def _load_offline(self, device):
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError:
            raise TranslationError("Install optional offline libraries: .venv\\Scripts\\python -m pip install -r requirements-translation.txt") from None
        if not (OFFLINE_DIR / "config.json").exists():
            raise TranslationError("VinAI model is not downloaded. Press Download model first.")
        selected = "cuda" if device == "auto" and torch.cuda.is_available() else ("cpu" if device == "auto" else device)
        if selected == "cuda" and not torch.cuda.is_available():
            raise TranslationError("PyTorch cannot use CUDA. Choose CPU or install a CUDA-enabled PyTorch build.")
        with self._lock:
            if self._offline is None or selected != self._offline_device:
                tokenizer = AutoTokenizer.from_pretrained(str(OFFLINE_DIR), src_lang="en_XX", local_files_only=True)
                model = AutoModelForSeq2SeqLM.from_pretrained(str(OFFLINE_DIR), local_files_only=True).to(selected).eval()
                self._offline, self._offline_device = (tokenizer, model), selected
        return self._offline

    def _translate_offline(self, text, config):
        tokenizer, model = self._load_offline(config.device)
        import torch
        inputs = tokenizer(text, return_tensors="pt", truncation=False)
        if inputs["input_ids"].shape[1] > 1024:
            raise TranslationError("This line is too long for VinAI. Split it into shorter sentences.")
        inputs = {k: v.to(self._offline_device) for k, v in inputs.items()}
        with self._lock, torch.inference_mode():
            result = model.generate(**inputs, decoder_start_token_id=tokenizer.lang_code_to_id["vi_VN"], max_new_tokens=768, num_beams=1)
        if len(result[0]) >= 768:
            raise TranslationError("VinAI output reached its limit. Split this line and retry.")
        return tokenizer.batch_decode(result, skip_special_tokens=True)[0]


def download_offline():
    if not all(importlib.util.find_spec(name) for name in ("torch", "transformers", "sentencepiece")):
        raise TranslationError("Install optional offline libraries: .venv\\Scripts\\python -m pip install -r requirements-translation.txt")
    try:
        from huggingface_hub import list_repo_files, snapshot_download
    except ImportError:
        raise TranslationError("Install offline libraries from requirements-translation.txt first.") from None
    OFFLINE_DIR.mkdir(parents=True, exist_ok=True)
    files = list_repo_files(OFFLINE_REPO)
    weights = "*.safetensors" if any(f.endswith(".safetensors") for f in files) else "*.bin"
    snapshot_download(OFFLINE_REPO, local_dir=str(OFFLINE_DIR),
                      allow_patterns=["*.json", "*.model", weights, "README.md", "LICENSE*"])


def check_offline_ready():
    if not all(importlib.util.find_spec(name) for name in ("torch", "transformers", "sentencepiece")):
        raise TranslationError("Install optional libraries from requirements-translation.txt before using VinAI.")
    if not (OFFLINE_DIR / "config.json").exists() or not (any(OFFLINE_DIR.glob("*.safetensors")) or any(OFFLINE_DIR.glob("*.bin"))):
        raise TranslationError("VinAI is not downloaded. Open Settings and press Download model.")

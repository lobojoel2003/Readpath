# SPDX-License-Identifier: Apache-2.0
"""Draft alt text with a local vision model (optional).

Supports Ollama (``/api/generate``) and any OpenAI-compatible server such as
llama.cpp, LM Studio or vLLM (``/v1/chat/completions``). Nothing leaves your
network unless you point it elsewhere. Configure with environment variables:

    READPATH_VISION_URL    e.g. http://localhost:11434
    READPATH_VISION_MODEL  e.g. qwen2.5vl:3b, llama3.2-vision, gemma3
    READPATH_VISION_API    "ollama" (default) or "openai"
    READPATH_VISION_KEY    optional bearer token for OpenAI-compatible servers

Drafts are always shown to a person to check before they are saved.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

PROMPT = (
    "You write alt text for images in public government documents so blind residents can use them. "
    "Describe what this image shows and why it matters, in one or two plain sentences, under 200 "
    "characters. If it is a chart, give the main trend or numbers. If it contains words, include them. "
    "Do not start with 'Image of' or 'Picture of'. Reply with the alt text only."
)


@dataclass
class VisionConfig:
    url: str
    model: str
    api: str = "ollama"
    key: str | None = None


def config_from_env() -> VisionConfig | None:
    url = os.environ.get("READPATH_VISION_URL", "").strip()
    if not url:
        return None
    return VisionConfig(
        url=url.rstrip("/"),
        model=os.environ.get("READPATH_VISION_MODEL", "qwen2.5vl:3b").strip(),
        api=os.environ.get("READPATH_VISION_API", "ollama").strip().lower(),
        key=os.environ.get("READPATH_VISION_KEY") or None,
    )


def available() -> bool:
    return config_from_env() is not None


def clean_alt(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = text.strip().strip('"“”').strip()
    text = re.sub(r"^(alt[\s-]*text|description)\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"^(an?\s+)?(image|picture|photo|graphic)\s+(of|showing)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text[:400]


def _post(url: str, payload: dict, key: str | None, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured by the admin
        return json.loads(resp.read().decode("utf-8"))


def suggest_alt_text(png: bytes, context: str = "", config: VisionConfig | None = None,
                     timeout: float = 180) -> str:
    """Ask the configured model for a draft description of an image."""
    config = config or config_from_env()
    if config is None:
        raise RuntimeError("No vision model is configured.")
    prompt = PROMPT
    if context:
        prompt += f"\n\nText near the image in the document: {context[:600]}"
    image_b64 = base64.b64encode(png).decode("ascii")
    try:
        if config.api == "openai":
            data = _post(f"{config.url}/v1/chat/completions", {
                "model": config.model,
                "max_tokens": 200,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ]}],
            }, config.key, timeout)
            text = data["choices"][0]["message"]["content"]
        else:
            data = _post(f"{config.url}/api/generate", {
                "model": config.model, "prompt": prompt, "images": [image_b64], "stream": False,
                "options": {"temperature": 0.2},
            }, config.key, timeout)
            text = data.get("response", "")
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(f"The vision model did not answer: {exc}") from exc
    alt = clean_alt(str(text))
    if not alt:
        raise RuntimeError("The vision model returned an empty description.")
    return alt

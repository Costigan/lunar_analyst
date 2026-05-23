from __future__ import annotations

import json
import sys


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except Exception:
        payload = {}
    model_id = str(payload.get("model_id", "local-subprocess-model"))
    conversation = payload.get("conversation", [])
    prompt = ""
    if isinstance(conversation, list):
        for item in reversed(conversation):
            if isinstance(item, dict) and str(item.get("role", "")) == "user":
                prompt = str(item.get("content", "")).strip()
                break
    text = (
        "Subprocess provider placeholder response.\n"
        f"Model: {model_id}\n"
        f"Latest user prompt: {prompt}"
    )
    out = {
        "text": text,
        "usage": {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(text) // 4)},
        "cache_attempted": False,
        "cache_applied": False,
    }
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

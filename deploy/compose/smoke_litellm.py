"""Smoke test for the dev LiteLLM proxy: one short request to the soc-fast alias.

The dev models are reached through OpenRouter, so the script runs only when OPENROUTER_API_KEY
is set (T-003); otherwise it reports that it skipped. It never sends that key: LiteLLM holds
it. Start the dev stack first, then, from the repository root:

    set -a; . deploy/compose/.env; set +a
    uv run python deploy/compose/smoke_litellm.py

Environment: LITELLM_MASTER_KEY (required), LITELLM_BASE_URL (default http://127.0.0.1:4000).
Exit status: 0 when soc-fast answered or the check was skipped, 1 when it failed.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

ALIAS = "soc-fast"
DEFAULT_BASE_URL = "http://127.0.0.1:4000"
PROMPT = "Reply with the single word: pong"
# Leaves room for the model's reasoning tokens before the answer.
MAX_TOKENS = 512
TIMEOUT_SECONDS = 120


def answer_text(reply: object) -> str:
    """Return the text of the first choice of a chat completion, or "" if there is none."""
    if not isinstance(reply, dict):
        return ""
    choices = reply.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content.strip() if isinstance(content, str) else ""


def main() -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("SKIPPED: OPENROUTER_API_KEY is not set, so the dev models are not reachable.")
        return 0
    master_key = os.environ.get("LITELLM_MASTER_KEY")
    if not master_key:
        print("FAILED: LITELLM_MASTER_KEY is not set.", file=sys.stderr)
        return 1
    base_url = os.environ.get("LITELLM_BASE_URL") or DEFAULT_BASE_URL
    if not base_url.startswith(("http://", "https://")):
        print(f"FAILED: LITELLM_BASE_URL is not an http(s) URL: {base_url}", file=sys.stderr)
        return 1

    body = {
        "model": ALIAS,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": MAX_TOKENS,
    }
    request = urllib.request.Request(  # noqa: S310 - the scheme is checked above
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            reply = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        print(f"FAILED: LiteLLM answered HTTP {error.code}: {detail}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:  # connection errors, timeouts, invalid JSON
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started

    text = answer_text(reply)
    if not text:
        print(
            f"FAILED: the response has no answer text: {json.dumps(reply)[:500]}", file=sys.stderr
        )
        return 1
    usage = reply.get("usage") if isinstance(reply, dict) else None
    tokens = usage.get("total_tokens", "?") if isinstance(usage, dict) else "?"
    print(f"OK: {ALIAS} answered in {elapsed:.1f}s ({tokens} tokens): {text[:200]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

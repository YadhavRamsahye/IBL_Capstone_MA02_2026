"""
tools/check_gemini.py
Find which Gemini models your key can actually generate with.

Why this is needed
------------------
Free-tier quota is granted **per model**, not per key. A valid AI Studio key
lists dozens of models and still returns

    429 RESOURCE_EXHAUSTED ... limit: 0, model: gemini-2.0-flash

for most of them — `limit: 0` meaning that model has no free-tier allocation on
this project at all, not that you used it up. Listing models tells you nothing
about this; the only reliable check is to send a real request.

This also reports thinking-token usage, which matters because Gemini 3 models
reason before answering and those tokens count against max_output_tokens. A cap
sized for the visible answer alone comes back empty or truncated.

Usage
-----
    python tools/check_gemini.py                # probe the usual candidates
    python tools/check_gemini.py --all-flash    # probe every flash variant
    python tools/check_gemini.py --model gemini-3-flash-preview

Put a model marked WORKS into GEMINI_MODEL in your .env.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv          # noqa: E402
load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

import os                               # noqa: E402

# Gemma is served by the same endpoint and needs no code change — it is just
# another value for GEMINI_MODEL. Included here so the trade-off is visible:
# measured on this project's prompt, Gemma answers at comparable quality but
# roughly 3-4x slower than Gemini 3 Flash.
CANDIDATES = [
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
]

PROMPT = "Reply with exactly two short sentences about traffic being heavy."


def probe(client, model: str, max_tokens: int) -> None:
    from google.genai import types, errors

    t0 = time.time()
    try:
        r = client.models.generate_content(
            model=model, contents=PROMPT,
            config=types.GenerateContentConfig(max_output_tokens=max_tokens),
        )
        u = getattr(r, "usage_metadata", None)
        thoughts = getattr(u, "thoughts_token_count", None) or 0
        out = getattr(u, "candidates_token_count", None) or 0
        text = (r.text or "").strip()

        if not text:
            print(f"  {model:26s} QUOTA OK but EMPTY  "
                  f"(thinking={thoughts}, visible={out}) — raise max_output_tokens")
            return
        print(f"  {model:26s} WORKS  {time.time()-t0:5.1f}s  "
              f"thinking={thoughts:4d} visible={out:3d}")
        print(f"  {'':26s}        {text[:80]}")
    except errors.ClientError as exc:
        msg = str(exc)
        if "limit: 0" in msg:
            print(f"  {model:26s} NO FREE QUOTA (limit: 0 for this model)")
        elif getattr(exc, "code", None) == 429:
            print(f"  {model:26s} RATE LIMITED (has quota — retry shortly)")
        elif getattr(exc, "code", None) == 404:
            print(f"  {model:26s} NOT AVAILABLE to this key")
        else:
            print(f"  {model:26s} ERROR {getattr(exc, 'code', '?')}: {msg[:60]}")
    except Exception as exc:
        print(f"  {model:26s} ERROR {type(exc).__name__}: {str(exc)[:60]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="probe one specific model")
    ap.add_argument("--all-flash", action="store_true",
                    help="probe every flash variant the key can list")
    ap.add_argument("--max-tokens", type=int, default=800,
                    help="output budget; must cover thinking tokens too (default 800)")
    args = ap.parse_args()

    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        sys.exit("GEMINI_API_KEY is not set in .env")

    try:
        from google import genai
    except ImportError:
        sys.exit("google-genai is not installed. Run: pip install google-genai")

    client = genai.Client(api_key=key)
    print(f"key {key[:6]}…{key[-4:]}  budget {args.max_tokens} tokens\n")

    if args.model:
        models = [args.model]
    elif args.all_flash:
        models = sorted(
            m.name.replace("models/", "") for m in client.models.list()
            if "flash" in m.name and "image" not in m.name and "tts" not in m.name
        )
    else:
        models = CANDIDATES

    print("Probing (one real request each):")
    for m in models:
        probe(client, m, args.max_tokens)
        time.sleep(1)          # stay clear of per-minute limits while probing

    print("\nPut a model marked WORKS into GEMINI_MODEL in your .env.")


if __name__ == "__main__":
    main()

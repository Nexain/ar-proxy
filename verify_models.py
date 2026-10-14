#!/usr/bin/env python3
"""Verify Trae's model routes through a running proxy, without logging API keys."""

import argparse
import getpass
import http.client
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit


def request_body(model, stream, tokens):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with only OK."}],
        "stream": stream,
    }
    body["max_completion_tokens" if model.startswith("gpt-") else "max_tokens"] = tokens
    return body


def probe(profile, base_url, key, stream, tokens=256, timeout=60):
    if not key:
        return False, "API key is required"
    base = urlsplit(base_url)
    route = urlsplit(profile["requestUrl"]).path
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise ValueError("Base URL must be an http(s) URL")
    connection_type = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
    conn = connection_type(base.hostname, base.port, timeout=timeout)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"}
    if profile["apiFormat"] == "Anthropic Messages":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = "Bearer " + key
    try:
        conn.request("POST", base.path.rstrip("/") + route,
                     body=json.dumps(request_body(profile["model"], stream, tokens)), headers=headers)
        response = conn.getresponse()
        content_type = (response.getheader("Content-Type") or "").lower()
        if response.status != 200:
            error = response.read(4096).decode("utf-8", "replace").replace(key, "<redacted>")
            return False, f"HTTP {response.status}: {error}"
        if not stream:
            if "json" not in content_type:
                return False, f"Expected JSON; received {content_type or 'no content type'}"
            data = json.loads(response.read())
            if data.get("error"):
                return False, json.dumps(data["error"]).replace(key, "<redacted>")
            if profile["apiFormat"] == "Anthropic Messages":
                text = "".join(x.get("text", "") for x in data.get("content", []) if x.get("type") == "text")
            else:
                text = "".join(x.get("message", {}).get("content") or "" for x in data.get("choices", []))
            return bool(text.strip()), "visible text received" if text.strip() else "No visible text (check output-token budget or model availability)"
        if "text/event-stream" not in content_type:
            return False, f"Expected SSE; received {content_type or 'no content type'}"
        visible, completed, payload = False, False, []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = response.readline()
            if not line:
                break
            line = line.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                value = line[5:]
                payload.append(value[1:] if value.startswith(" ") else value)
            elif not line and payload:
                value = "\n".join(payload)
                payload = []
                if value == "[DONE]":
                    completed = True
                    break
                data = json.loads(value)
                if not isinstance(data, dict):
                    return False, "Non-object SSE payload"
                if data.get("error") or data.get("type") == "error":
                    return False, json.dumps(data).replace(key, "<redacted>")
                if data.get("type") == "message_stop":
                    completed = True
                    break
                visible |= bool(data.get("delta", {}).get("text", "").strip())
                for choice in data.get("choices", []):
                    visible |= bool((choice.get("delta", {}).get("content") or "").strip())
        return visible and completed, f"visible text={visible}, completion marker={completed}"
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return False, str(exc).replace(key, "<redacted>")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8317")
    parser.add_argument("--model", help="Test only this model ID from trae-models.json")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    profiles = json.loads(Path(__file__).with_name("trae-models.json").read_text())
    if args.model:
        profiles = [p for p in profiles if p["model"] == args.model]
        if not profiles:
            parser.error("Model is not in trae-models.json")
    if args.max_tokens <= 0 or args.timeout <= 0:
        parser.error("Token limit and timeout must be positive")
    key = os.environ.get("AGENTROUTER_API_KEY") or getpass.getpass("AgentRouter API key: ")
    if not key:
        parser.error("API key is required")
    failed = False
    for profile in profiles:
        for stream in (False, True):
            ok, detail = probe(profile, args.base_url, key, stream, args.max_tokens, args.timeout)
            failed |= not ok
            print(f"{'PASS' if ok else 'FAIL'} {profile['model']} {'stream' if stream else 'JSON'}: {detail}", flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

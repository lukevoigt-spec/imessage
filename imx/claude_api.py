"""Talking to Anthropic's API, and looking after the API key.

Everything that sends message text off this Mac goes through `send()` in this
file. Nothing else in the tool contacts Anthropic.

Why this talks to the API directly instead of using Anthropic's Python library:
the library has to be installed from Terminal, and which version you get
depends on the Python that came with your Mac. Sending the request directly
needs nothing installed and behaves the same on every Mac. Requests go out
through the Mac's own `curl`, which trusts the same certificates as Safari.

The API key lives in the macOS Keychain (or the ANTHROPIC_API_KEY environment
variable). It is never written to a file, never put in this code folder, and is
handed to curl on its standard input so it never shows up in the list of
running programs.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import paths

API_VERSION = "2026-10-21"
KEYCHAIN_SERVICE = "imessage-explorer-anthropic"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# US dollars per million tokens (input, output) at the standard rate, as published on 3 November 2026.
# Batch processing is half these. Used to work out what each request cost from the token counts
# Anthropic reports back, so spending is measured rather than guessed.
PRICES: Dict[str, Tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
MODEL_NAMES = {"claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5"}

_KEY_SHAPE = re.compile(r"^[A-Za-z0-9_\-]{20,300}$")


class AIError(Exception):
    """A request to Anthropic could not be completed. The message says why, plainly.

    `status` is the HTTP status (0 when the service was never reached), `retry_after` the number
    of seconds the service asked us to wait, and `retryable` whether trying again can help.
    """

    def __init__(self, message: str, status: int = 0, kind: str = "", retry_after: Optional[float] = None,
                 retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.retry_after = retry_after
        self.retryable = retryable


def base_url() -> str:
    """Anthropic's address. Tests point this at a stand-in running on the same machine."""
    return os.environ.get("IMX_ANTHROPIC_URL", "https://api.anthropic.com").rstrip("/")


# ---- the API key ----------------------------------------------------------------------------

def api_key() -> Optional[str]:
    """Find the API key: environment first, then the macOS Keychain. Never a file."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    if shutil.which("security"):
        try:
            result = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def save_key(key: str) -> None:
    """Store the API key in the macOS Keychain, replacing any earlier one, and confirm it reads back."""
    key = (key or "").strip()
    if not _KEY_SHAPE.match(key):
        raise AIError("That does not look like an Anthropic API key. Copy the whole key, which starts with sk-ant-, "
                      "with no spaces.")
    if not shutil.which("security"):
        raise AIError("Storing the key needs the macOS Keychain, which was not found on this computer.")
    account = getpass.getuser()
    command = 'add-generic-password -U -a "%s" -s %s -w %s\n' % (account.replace('"', ""), KEYCHAIN_SERVICE, key)
    try:
        # First choice: hand the command to `security` on its input, so the key never appears in
        # the list of running programs.
        subprocess.run(["security", "-i"], input=command, capture_output=True, text=True, timeout=30)
        if _stored_key() != key:
            # Some versions of macOS do not accept that form. This one shows the key for an
            # instant to other programs running under your own account.
            subprocess.run(["security", "add-generic-password", "-U", "-a", account, "-s", KEYCHAIN_SERVICE, "-w", key],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AIError("The Keychain could not be reached (%s)." % exc)
    if _stored_key() != key:
        raise AIError("The key could not be saved to the Keychain. If macOS showed a question about the Keychain, "
                      "allow it and try again.")


def _stored_key() -> Optional[str]:
    try:
        result = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def forget_key() -> None:
    """Remove the stored key from the Keychain."""
    if shutil.which("security"):
        subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE], capture_output=True, timeout=15)


# ---- sending a request ---------------------------------------------------------------------------

def _config_line(name: str, value: str) -> str:
    return 'header = "%s: %s"\n' % (name, value.replace("\\", "\\\\").replace('"', '\\"'))


def _curl(method: str, path: str, body: Optional[dict], key: str, betas: Tuple[str, ...] = (),
          timeout: int = 600) -> Tuple[int, Dict[str, str], bytes]:
    """One HTTP request through the system's curl. Returns (status, headers, body)."""
    scratch = Path(tempfile.mkdtemp(prefix="request-", dir=str(paths.data_dir())))
    try:
        config = _config_line("x-api-key", key) + _config_line("anthropic-version", API_VERSION)
        if betas:
            config += _config_line("anthropic-beta", ",".join(betas))
        command = ["curl", "-sS", "--max-time", str(timeout), "-X", method, "--config", "-",
                   "-D", str(scratch / "headers"), "-o", str(scratch / "reply"), "-w", "%{http_code}"]
        if body is not None:
            # The request holds message text. It sits in your private data folder for the length
            # of the request and is deleted straight after.
            (scratch / "body.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            config += _config_line("content-type", "application/json")
            command += ["--data-binary", "@" + str(scratch / "body.json")]
        command.append(base_url() + path)
        try:
            result = subprocess.run(command, input=config.encode("utf-8"), capture_output=True, timeout=timeout + 30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AIError("Could not reach Anthropic (%s). Check the internet connection." % exc, retryable=True)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no detail"]
            raise AIError("Could not reach Anthropic: %s" % detail[0], retryable=True)
        try:
            status = int(result.stdout.decode("ascii", "replace").strip()[-3:])
        except ValueError:
            raise AIError("Anthropic's reply could not be read.", retryable=True)
        headers: Dict[str, str] = {}
        try:
            for line in (scratch / "headers").read_text(encoding="utf-8", errors="replace").splitlines():
                if ":" in line:
                    name, value = line.split(":", 1)
                    headers[name.strip().lower()] = value.strip()
        except OSError:
            pass
        try:
            reply = (scratch / "reply").read_bytes()
        except OSError:
            reply = b""
        return status, headers, reply
    finally:
        shutil.rmtree(str(scratch), ignore_errors=True)


Transport = Callable[..., Tuple[int, Dict[str, str], bytes]]


def _request(method: str, path: str, body: Optional[dict], betas: Tuple[str, ...] = (),
             transport: Optional[Transport] = None, lines: bool = False):
    """One request to Anthropic. Returns the reply as a dict, or with `lines` as a list of dicts (one per line)."""
    key = api_key()
    if not key:
        raise AIError("No Anthropic API key has been saved yet. Add one on the Reading screen.", status=401,
                      kind="authentication_error")
    status, headers, reply = (transport or _curl)(method, path, body, key, betas)
    if status == 200 and lines:
        found = []
        for line in reply.decode("utf-8", "replace").splitlines():
            if line.strip():
                try:
                    found.append(json.loads(line))
                except ValueError:
                    continue
        return found
    try:
        payload = json.loads(reply.decode("utf-8")) if reply else {}
    except ValueError:
        payload = {}
    if status == 200 and isinstance(payload, dict):
        return payload
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    kind = str(error.get("type") or "")
    detail = str(error.get("message") or "no detail given")
    retry_after: Optional[float] = None
    try:
        retry_after = float(headers.get("retry-after", ""))
    except ValueError:
        pass
    if status == 401:
        raise AIError("Anthropic did not accept the API key. Check it was copied in full, or create a new one.",
                      status, kind)
    if status in (402, 403):
        raise AIError("Anthropic refused this key: %s" % detail, status, kind)
    if status == 404:
        raise AIError("Anthropic could not find what was asked for: %s" % detail, status, kind)
    if status == 429:
        # A rate limit comes with a wait time. Without one, the account has hit a spending limit
        # and waiting will not help.
        if retry_after is None:
            raise AIError("The Anthropic account has reached a limit: %s" % detail, status, kind)
        raise AIError("Anthropic asked us to slow down.", status, kind, retry_after=retry_after, retryable=True)
    if status in (408, 409) or status >= 500:
        raise AIError("Anthropic is having trouble right now (%s). Try again shortly." % status, status, kind,
                      retry_after=retry_after, retryable=True)
    raise AIError("Anthropic rejected the request: %s" % detail, status, kind)


def cost_usd(model: str, usage: dict) -> float:
    """What a reply cost, from the token counts Anthropic reports. 0 for a model with no listed price."""
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    tokens_in = sum(int(usage.get(name) or 0) for name in
                    ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return (tokens_in * price_in + int(usage.get("output_tokens") or 0) * price_out) / 1_000_000.0


def message_body(model: str, system: str, user_text: str, schema: Optional[dict] = None, effort: str = "medium",
                 max_tokens: int = 16000) -> Dict[str, object]:
    """The request for one message, as Anthropic expects it. Used for single requests and inside batches alike."""
    output_config: Dict[str, object] = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "output_config": output_config,
        "messages": [{"role": "user", "content": user_text}],
    }


def read_message(payload: dict, asked_model: str, price_factor: float = 1.0) -> Dict[str, object]:
    """Turn Anthropic's reply into the few things the app uses: text, why it stopped, who answered, tokens and cost."""
    blocks = payload.get("content") if isinstance(payload.get("content"), list) else []
    text = "".join(str(block.get("text") or "") for block in blocks
                   if isinstance(block, dict) and block.get("type") == "text").strip()
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    answered_by = str(payload.get("model") or asked_model)
    return {
        "text": text,
        "stop_reason": str(payload.get("stop_reason") or ""),
        "model": answered_by,
        "usage": {"input_tokens": int(usage.get("input_tokens") or 0), "output_tokens": int(usage.get("output_tokens") or 0)},
        # Priced at the model that answered when we know its price, else the one that was asked.
        "cost_usd": cost_usd(answered_by if answered_by in PRICES else asked_model, usage) * price_factor,
    }


def send(model: str, system: str, user_text: str, schema: Optional[dict] = None, effort: str = "medium",
         max_tokens: int = 16000, fallbacks: bool = False, transport: Optional[Transport] = None) -> Dict[str, object]:
    """Send one request to Claude and return its reply.

    The reply holds: `text`, `stop_reason`, `model` (the model that actually answered), `usage`
    (token counts) and `cost_usd`. A reply whose `stop_reason` is "refusal" means the model
    declined; it is returned, not raised, because it was still billed.
    """
    body = message_body(model, system, user_text, schema, effort, max_tokens)
    betas: Tuple[str, ...] = ()
    if fallbacks:
        # If the model declines, let the service retry on its default fallback model.
        body["fallbacks"] = "default"
        betas = (FALLBACK_BETA,)
    return read_message(_request("POST", "/v1/messages", body, betas, transport), model)


# ---- batches: many requests sent together, processed within a day, at half price -----------------------

BATCH_PRICE_FACTOR = 0.5


def batch_create(requests: List[Dict[str, object]], transport: Optional[Transport] = None) -> Dict[str, object]:
    """Hand Anthropic a batch of requests. Each is {"custom_id": ..., "params": message_body(...)}. Returns the batch."""
    return _request("POST", "/v1/messages/batches", {"requests": requests}, (), transport)


def batch_get(batch_id: str, transport: Optional[Transport] = None) -> Dict[str, object]:
    """Where a batch has got to. `processing_status` is "ended" when every request has finished."""
    return _request("GET", "/v1/messages/batches/%s" % batch_id, None, (), transport)


def batch_results(batch_id: str, transport: Optional[Transport] = None) -> List[Dict[str, object]]:
    """Every result of a finished batch, in no particular order. Match them up by `custom_id`."""
    return _request("GET", "/v1/messages/batches/%s/results" % batch_id, None, (), transport, lines=True)


def batch_list(pages: int = 5, transport: Optional[Transport] = None) -> List[Dict[str, object]]:
    """The most recent batches Anthropic holds for this key, newest first (up to 100 per page)."""
    found: List[Dict[str, object]] = []
    after = ""
    for _ in range(pages):
        page = _request("GET", "/v1/messages/batches?limit=100" + ("&after_id=" + after if after else ""), None, (), transport)
        rows = page.get("data") if isinstance(page.get("data"), list) else []
        found.extend(row for row in rows if isinstance(row, dict))
        after = str(page.get("last_id") or "")
        if not page.get("has_more") or not after:
            break
    return found


def batch_cancel(batch_id: str, transport: Optional[Transport] = None) -> Dict[str, object]:
    return _request("POST", "/v1/messages/batches/%s/cancel" % batch_id, {}, (), transport)


def send_patiently(*args, attempts: int = 6, sleep: Callable[[float], None] = time.sleep,
                   keep_going: Callable[[], bool] = lambda: True, on_wait: Optional[Callable[[float, int], None]] = None,
                   **kwargs) -> Dict[str, object]:
    """Like send(), but waits and tries again when Anthropic says it is busy or asks us to slow down.

    `on_wait(seconds, status)` is told about each wait, so the time lost to waiting can be recorded.
    """
    wait = 2.0
    for attempt in range(attempts):
        try:
            return send(*args, **kwargs)
        except AIError as exc:
            if not exc.retryable or attempt == attempts - 1 or not keep_going():
                raise
            pause = min(exc.retry_after if exc.retry_after is not None else wait, 90.0)
            wait = min(wait * 2, 60.0)
            if on_wait is not None:
                on_wait(pause, exc.status)
            sleep(pause)
    raise AIError("Anthropic stayed busy. Try again later.", retryable=True)  # not reached; keeps type checkers calm


def check_models(models: List[str], transport: Optional[Transport] = None) -> Dict[str, bool]:
    """Ask Anthropic whether this key can use each model. Sends no message text and costs nothing."""
    available: Dict[str, bool] = {}
    for model in models:
        try:
            _request("GET", "/v1/models/%s" % model, None, (), transport)
            available[model] = True
        except AIError as exc:
            if exc.status == 404:
                available[model] = False
            else:
                raise
    return available


def status() -> Dict[str, object]:
    """Whether a key is saved. Does not contact Anthropic."""
    return {"key_found": api_key() is not None, "can_store_key": bool(shutil.which("security"))}

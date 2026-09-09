#!/usr/bin/env python3
"""Fallback plan reviewer over any OpenAI-compatible chat endpoint.

Part of the claudex-loop quota-exhaustion protocol (see FALLBACK.md). Invoked
ONLY after the user explicitly chose to switch reviewers — never automatically,
never silently. Configuring a chain order in CLAUDEX_REVIEWERS is that consent
in standing form: `--chain` walks the configured order and REPORTS every
provider it skipped and why; it never invents a reviewer the user did not list.

Safety model (the two findings from issue #7, addressed by construction):

1. "Read-only" is not read off vendor docs — it is structural. The model gets
   NO file or tool access at all: the plan (and optionally the review log so
   far) is inlined into the prompt, the reply is text. There is nothing to
   escalate, so per-vendor sandbox audits are unnecessary.
2. A reply must end with a VERDICT line; a reply without one is an INVALID
   review (exit 3), not an approval. Zero findings is a legitimate verdict --
   a sound plan may have no defects, and a finding count is not a quality
   score. The optional rubber-stamp gate (--min-findings, default 0) is off
   unless asked for: set CLAUDEX_FALLBACK_MIN_FINDINGS=3 when the substitute
   is a small local model, where "looks good to me" is a real failure mode and
   there is no structured-output validation to catch it.

Plan-hash binding: the SHA256 of the plan file is stamped into the verdict
header. A verdict is only valid for exactly that plan state; editing the plan
afterwards self-demotes the approval.

Configuration is environment-driven (process env, plus a `.env` file in the
working directory if present). Reviewers are named profiles:

    CLAUDEX_REVIEWERS=lmstudio,openrouter          # chain order, first = default
    CLAUDEX_REVIEWER_LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1
    CLAUDEX_REVIEWER_LMSTUDIO_MODEL=qwen/qwen3.8-27b
    CLAUDEX_REVIEWER_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
    CLAUDEX_REVIEWER_OPENROUTER_MODEL=deepseek/deepseek-r1
    CLAUDEX_REVIEWER_OPENROUTER_API_KEY_ENV=OPENROUTER_API_KEY

Any OpenAI-compatible endpoint works: LM Studio, Ollama, OpenRouter, OpenAI,
Google Gemini (…/v1beta/openai) and Anthropic (api.anthropic.com/v1) included.
See .env.example for ready-made profiles.

Egress, in three tiers — plain http reaches loopback only in all of them. The
plan and the whole review log travel in that request, so the old rule ("no
cleartext *if a key is set*") had it backwards and left keyless endpoints
unprotected.

1. Nothing configured: the scheme rule alone. Stdlib-only, the default.
2. CLAUDEX_EGRESS_ALLOW=host,host — an exact-match host list. No wildcards and
   no suffixes: `api.openai.com.attacker.test` ends in an allowed name.
3. A file allowlist — CLAUDEX_EGRESS_ALLOWLIST=<path>, or `config/allowed_egress.yaml`
   at the repo root, whose mere presence is the opt-in. Per-host schemes and a
   place to write down WHY a host is listed. **Fail-closed:** if the file is
   named but missing, unparseable, or PyYAML is not installed, nothing is sent.
   Not installing the parser does not lift the restriction. This is the only
   tier that needs a dependency, and only for whoever chooses it.

Preflight (`--check`, and automatically per provider in `--chain`): local
endpoints (127.0.0.1/localhost) only need to be reachable; remote providers
get an auth-validity probe (GET /models), and OpenRouter — the only listed
provider with a real balance API — additionally reports remaining credits via
GET /credits. OpenAI/Gemini/Anthropic expose no balance endpoint: exhaustion
there only shows up as HTTP 429/402 on use, which the chain treats as
terminal for that provider and moves on.

Usage:
    python scripts/fallback_review.py --plan PLAN.md [--reviewer NAME | --chain]
        [--log PLAN-REVIEW-LOG.md] [--round N] [--out verdict.txt]
        [--min-findings N] [--list] [--check]

Exit codes: 0 = valid review with verdict; 2 = config/transport error (or, in
--chain mode, every configured provider failed preflight or errored); 3 =
invalid review (no verdict line, or rubber-stamp suspicion). Never treat exit
2/3 as an approval.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

SYSTEM_PROMPT = (
    "You are an adversarial reviewer for an implementation plan. Be skeptical "
    "and specific — your job is to find what breaks, not to be agreeable. "
    "Identify concrete flaws: security holes, race conditions, missing edge "
    "cases, schema conflicts, wrong assumptions, observability gaps, simpler "
    "alternatives. Number your findings; anchor each one to a section or line "
    "of the plan and state what concretely goes wrong if the plan ships as-is, "
    "plus a one-line fix. You only see the text you are given — if a claim "
    "cannot be verified from the plan itself, flag it as unverified rather "
    "than assuming it holds. Do NOT reproduce, re-list or line-number the "
    "material you were given — cite it by section or file:line and move on; "
    "your output budget is for findings. End your reply with EXACTLY one line: "
    "'VERDICT: APPROVED' if the plan is sound enough to implement, or "
    "'VERDICT: REVISE' if it still has material problems."
)


def load_dotenv(path=".env"):
    """Minimal .env loader — process env wins over file values."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8-sig") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except OSError:
        pass


def reviewer_names():
    names = [n.strip() for n in os.environ.get("CLAUDEX_REVIEWERS", "").split(",") if n.strip()]
    if names:
        return names
    # No explicit order: derive from any configured profile blocks.
    found = set()
    for key in os.environ:
        m = re.match(r"CLAUDEX_REVIEWER_([A-Z0-9_]+)_BASE_URL$", key)
        if m:
            found.add(m.group(1).lower())
    return sorted(found)


def profile(name):
    prefix = "CLAUDEX_REVIEWER_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_"

    def get(suffix, default=None):
        return os.environ.get(prefix + suffix, default)

    base_url = get("BASE_URL")
    model = get("MODEL")
    if not base_url or not model:
        raise SystemExit(
            f"Reviewer '{name}' is not configured: set {prefix}BASE_URL and "
            f"{prefix}MODEL (env or .env). See .env.example.")
    api_key = get("API_KEY")
    key_env = get("API_KEY_ENV")
    if api_key:
        # Was a warning until 2026-08-28. A warning nobody reads is not
        # enforcement, and the key sits in a file the whole time it is ignored.
        raise SystemExit(
            f"Reviewer '{name}': {prefix}API_KEY holds the key inline. Use "
            f"{prefix}API_KEY_ENV=<VARNAME> instead and let your secret manager "
            "inject that variable, so the key never sits in a file.")
    if not api_key and key_env:
        api_key = os.environ.get(key_env)
        if not api_key:
            raise SystemExit(
                f"Reviewer '{name}': {prefix}API_KEY_ENV points at '{key_env}' "
                "but that variable is empty. Refusing to call without the key.")
    base_url = base_url.rstrip("/")
    try:
        check_egress(base_url)
    except (EgressDenied, AllowlistUnreadable) as exc:
        raise SystemExit(f"Reviewer '{name}': {exc}") from exc

    def num(suffix, default, cast):
        raw = get(suffix, default)
        try:
            return cast(raw)
        except (TypeError, ValueError):
            raise SystemExit(
                f"Reviewer '{name}': {prefix}{suffix}='{raw}' is not a number.") from None

    return {
        "name": name,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "temperature": num("TEMPERATURE", "0.7", float),
        "max_tokens": num("MAX_TOKENS", "8192", int),
        "timeout": num("TIMEOUT", "1800", int),
        # Opt-in, local endpoints only: ask the runtime to load the model first.
        "autoload": (get("AUTOLOAD", "") or "").strip().lower() in ("1", "true", "yes", "on"),
        "context": num("CONTEXT", "32768", int),
    }


def auth_headers(p):
    headers = {"Content-Type": "application/json"}
    if p["api_key"]:
        headers["Authorization"] = "Bearer " + p["api_key"]
        if "anthropic" in (urlsplit(p["base_url"]).hostname or ""):
            # Anthropic also accepts its native header form; sending both is
            # harmless on the compatibility endpoint and required on native ones.
            headers["x-api-key"] = p["api_key"]
            headers["anthropic-version"] = "2023-06-01"
    return headers


# Names that MAY be loopback -- membership here is a shortcut, not the proof.
# The proof is _resolves_to_loopback(), which asks the resolver.
#
# `host.docker.internal` is gone from this set. It is an ordinary DNS name: on
# Docker Desktop it points at the host ACROSS A BRIDGE, and elsewhere it resolves
# to whatever the resolver says -- so trusting the spelling let the plan travel in
# the clear to somewhere that was not this machine (audit 2026-08-30, HIGH). The
# first fix kept the name and verified it by resolution, which was still wrong in
# one place: _opener() keyed its proxy bypass on membership here rather than on
# the verified answer, so that name got its proxy stripped while not being local
# at all (CodeRabbit, 2026-08-30). Anyone who really needs it names it in the
# allowlist, like any other remote host.
#
# The same objection applies to `localhost` in a tampered hosts file, which is why
# even these three are verified rather than trusted. Use _is_loopback_host().
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Ceilings for the local-runtime CLI. Neither call had one until 2026-08-30, so a
# hung `lms` blocked the entire chain past every configured reviewer timeout.
LMS_TIMEOUT = 30
LMS_LOAD_TIMEOUT = 600

# The only /models failures that mean "this compat layer has no such route"
# rather than "this provider is broken".
COMPAT_TOLERATED_CODES = frozenset({404, 405})

# The exact wording FALLBACK.md requires in every logged round heading. It names
# the limitation the reader has to see: this reviewer got the plan text and
# nothing else -- no repo, no tools.
FALLBACK_LABEL = "fallback — plan-text only, no repo access"

EGRESS_ALLOW_ENV = "CLAUDEX_EGRESS_ALLOW"
EGRESS_FILE_ENV = "CLAUDEX_EGRESS_ALLOWLIST"

# Looked for from the repo root when no file is named explicitly. Its mere
# presence is the opt-in: a repo that ships this file means the rule.
DEFAULT_ALLOWLIST_RELATIVE = os.path.join("config", "allowed_egress.yaml")


class EgressDenied(Exception):
    """The destination is not allowed to receive the plan."""


class AllowlistUnreadable(Exception):
    """The allowlist was asked for but cannot be read.

    Deliberately distinct from EgressDenied and deliberately fatal: "I cannot
    tell whether this is allowed" must not degrade into "go ahead". A missing
    file, broken YAML or a missing parser all land here.
    """


def _repo_root(start=None):
    here = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def _allowlist_source():
    """Where the host allowlist comes from: (path, explicit) or (None, False).

    Resolved at call time, not as a default argument — a module-level default
    binds at import and cannot be redirected in a test.
    """
    named = os.environ.get(EGRESS_FILE_ENV, "").strip()
    if named:
        return named, True
    root = _repo_root()
    if root:
        candidate = os.path.join(root, DEFAULT_ALLOWLIST_RELATIVE)
        if os.path.exists(candidate):
            return candidate, False
    return None, False


def _yaml_module():
    """Imported here, not at module scope: the parser is only needed by whoever
    actually keeps a file allowlist. Everyone else stays stdlib-only."""
    try:
        import yaml
    except ImportError as exc:
        raise AllowlistUnreadable(
            "a file allowlist is configured but PyYAML is not installed. Install "
            "it, or drop the file and use " + EGRESS_ALLOW_ENV + " instead. Not "
            "installing it does NOT lift the restriction.") from exc
    return yaml


def load_allowlist(path):
    """Read the allowlist file and return {host: {allowed schemes}}.

    Format (the one already in the wild):

        version: 1
        hosts:
          - host: 127.0.0.1
            schemes: [http, https]
            why: LM Studio, loopback only
    """
    yaml = _yaml_module()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise AllowlistUnreadable(f"{path} cannot be read: {exc}") from exc
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise AllowlistUnreadable(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("hosts"), list):
        raise AllowlistUnreadable(f"{path}: expected a mapping with a 'hosts' list")

    allowed = {}
    for entry in doc["hosts"]:
        if not isinstance(entry, dict) or not entry.get("host"):
            raise AllowlistUnreadable(f"{path}: entry without 'host': {entry!r}")
        schemes = entry.get("schemes") or ["https"]
        if isinstance(schemes, str):
            schemes = [schemes]
        allowed[str(entry["host"]).lower()] = frozenset(s.lower() for s in schemes)
    if not allowed:
        raise AllowlistUnreadable(f"{path}: no hosts listed")
    return allowed


def _resolves_to_loopback(host):
    """True only when EVERY address this name resolves to is a loopback address.

    A name is not a location. `host.docker.internal` was trusted by spelling and
    points across a bridge; `localhost` can be redirected in a hosts file. Asking
    the resolver is the only version of this check that means anything, and
    "every address" rather than "any" so a name with one loopback answer and one
    public answer does not pass (audit 2026-08-30).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    addresses = {info[4][0] for info in infos}
    if not addresses:
        return False
    for address in addresses:
        try:
            if not ipaddress.ip_address(address.split("%", 1)[0]).is_loopback:
                return False
        except ValueError:
            return False
    return True


def _is_loopback_host(host):
    """The one loopback test. Name plausible AND resolver agrees.

    Every caller uses this: check_egress for policy, _opener for the proxy
    bypass, is_local for the profile. They disagreed once -- _opener trusted the
    name alone -- and that is exactly how a bridge address got treated as local.
    """
    host = (host or "").lower()
    return host in LOOPBACK_HOSTS and _resolves_to_loopback(host)


def check_egress(url):
    """Refuse a destination before anything is sent to it.

    Three rules now, and the middle one is new:

    1. **Plain http only to VERIFIED loopback.** The check this replaced fired
       only when an API key was set — so a keyless endpoint received the plan and
       the whole review log in the clear, to any host. The protected content is
       the plan, not the key. (Sister-repo audit, 2026-08-28.) And "loopback" is
       now decided by the resolver, not by the hostname's spelling.
    2. **A remote host needs an allowlist. Absence is refusal, not permission.**
       This is the fix from 2026-08-30 (HIGH). With no allowlist file and
       CLAUDEX_EGRESS_ALLOW unset, ANY https host used to be accepted, and the
       error text even suggested unsetting the variable "to drop the host
       restriction". A repo-supplied `.env` could therefore name an arbitrary
       provider and the plan, the review log and whatever files the caller passed
       went there. An allowlist that defaults to open is a comment.
    3. **Exact host match** — no wildcards and no suffix matching:
       `api.openai.com.attacker.test` ends in an allowed name and would otherwise
       slip through.

    Raises EgressDenied. Never logs and continues — an allowlist you can ignore
    is not one.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    scheme = (parts.scheme or "").lower()
    if not host or not scheme:
        raise EgressDenied(f"destination has no host or scheme: {url!r}")

    if scheme not in ("http", "https"):
        raise EgressDenied(f"scheme '{scheme}' is not allowed for review traffic.")

    local = _is_loopback_host(host)
    if local:
        # Verified loopback needs no allowlist, whichever allowlist exists.
        # Previously it needed one as soon as ANY was configured -- so adding
        # `openrouter.ai` to reach a remote reviewer silently broke the local LM
        # Studio profile, while loopback stayed free for anyone who had
        # configured nothing at all. A rule that tightens as you configure more
        # policy is a trap. (CodeRabbit, 2026-08-30.)
        return

    path, explicit = _allowlist_source()
    if path:
        if explicit and not os.path.exists(path):
            raise AllowlistUnreadable(
                f"{EGRESS_FILE_ENV} points at '{path}', which does not exist. "
                "Refusing to send anything rather than treating a missing "
                "allowlist as permission.")
        allowed = load_allowlist(path)
        schemes = allowed.get(host)
        if schemes is None:
            raise EgressDenied(
                f"host '{host}' is not in the egress allowlist {path}. Add it "
                "deliberately, with a reason — that is the question the review "
                "will ask anyway.")
        if scheme not in schemes:
            raise EgressDenied(
                f"scheme '{scheme}' is not allowed for host '{host}' in {path} "
                f"(allowed: {', '.join(sorted(schemes))}).")
    else:
        raw_allow = os.environ.get(EGRESS_ALLOW_ENV, "").strip()
        names = {h.strip().lower() for h in raw_allow.split(",") if h.strip()}
        if names:
            if host not in names:
                raise EgressDenied(
                    f"host '{host}' is not in {EGRESS_ALLOW_ENV} "
                    f"({', '.join(sorted(names))}). Add it deliberately.")
        elif not local:
            raise EgressDenied(
                f"no egress allowlist is configured, so '{host}' is refused. This "
                f"fails CLOSED on purpose: the plan, the review log and any file "
                f"you passed would go to that host.\n"
                f"  Name it deliberately, either way:\n"
                f"    {EGRESS_ALLOW_ENV}={host}\n"
                f"  or a config/allowed_egress.yaml entry with a reason. Loopback "
                f"endpoints need neither.")

    # The floor, checked last so a more specific message wins: cleartext never
    # leaves the machine, no matter what any allowlist says.
    if scheme == "http" and not local:
        detail = (
            f"'{host}' is a name this machine does not resolve to a loopback address"
            if host in LOOPBACK_HOSTS
            else f"the non-loopback host '{host}'"
        )
        raise EgressDenied(
            f"refusing plain http to {detail}. The plan and the review log would "
            f"travel in the clear, whether or not a key goes with them. Use https.")


def is_local(p):
    return _is_loopback_host(urlsplit(p["base_url"]).hostname)


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Follow nothing. A redirect is a destination nobody checked.

    check_egress() vets the URL we chose; urlopen's default handler then follows
    30x hops without asking again, and Python re-sends the Authorization header
    on a same-scheme redirect. An allowlisted provider could therefore bounce the
    plan -- and the API key -- to a host that is not on the list (audit
    2026-08-30, HIGH). No LLM endpoint here has any business redirecting, so this
    refuses rather than re-validating: fewer moving parts, same guarantee.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EgressDenied(
            f"{req.full_url} answered {code} redirecting to {newurl}. Redirects are "
            f"not followed: the new host was never checked against the allowlist, "
            f"and the credentials would travel with the request. Point the profile "
            f"straight at the endpoint that answers."
        )


def _opener(url):
    """An opener that follows no redirect and proxies no loopback request.

    Proxy environment (HTTP_PROXY/ALL_PROXY) is honoured by urllib for loopback
    URLs too unless no_proxy happens to cover them, which would route a cleartext
    `http://127.0.0.1` request through whatever that variable names -- with the
    plan in it. Loopback traffic gets an empty ProxyHandler; remote traffic is
    https-only anyway, so a proxy sees a CONNECT tunnel and nothing else.
    """
    handlers = [_RefuseRedirects()]
    # _is_loopback_host, not membership in LOOPBACK_HOSTS: stripping the proxy
    # for a name that merely LOOKS local is how a bridge address got treated as
    # this machine. (CodeRabbit, 2026-08-30.)
    if _is_loopback_host(urlsplit(url).hostname):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def http_get_json(url, headers, timeout=15):
    check_egress(url)  # every request, not just the one the profile declared
    req = urllib.request.Request(url, headers=headers)
    with _opener(url).open(req, timeout=timeout) as resp:
        return json.load(resp)


def ensure_model_loaded(p):
    """Have the local runtime load this profile's model, if the profile asked.

    Returns (ok, detail). A no-op — (True, "") — unless ALL of these hold: the
    profile sets AUTOLOAD, the endpoint is local, and `lms` is on PATH. The
    adapter is generic and must not assume LM Studio is what is listening.

    Why it exists at all: preflight probes GET /models, and LM Studio lists the
    models it has DOWNLOADED, not the one it has LOADED. So the check goes green
    and the run fails afterwards. This was the one thing the LM-Studio-specific
    predecessor did better than the generic adapter; here it is, as an option.
    """
    if not p.get("autoload") or not is_local(p):
        return True, ""
    if not shutil.which("lms"):
        return False, ("AUTOLOAD is set but the `lms` CLI is not on PATH — cannot "
                       "load the model. Load it in LM Studio, or unset AUTOLOAD.")

    model, context = p["model"], p["context"]
    root = p["base_url"].rsplit("/v1", 1)[0]
    try:
        data = http_get_json(root + "/api/v0/models", {})
        for entry in data.get("data", []):
            if entry.get("id") != model or entry.get("state") != "loaded":
                continue
            if (entry.get("loaded_context_length") or 0) >= context:
                return True, f"{model} already loaded"
            # Loaded, but too small a window for a plan plus the log so far.
            try:
                subprocess.run(
                    ["lms", "unload", model], capture_output=True, timeout=LMS_TIMEOUT
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                return False, f"`lms unload {model}` did not finish: {exc}"
            break
    except (EgressDenied, AllowlistUnreadable):
        raise
    except (urllib.error.URLError, OSError, ValueError):
        pass  # no /api/v0 here; let the load attempt below be the verdict

    # Bounded on purpose. Without a timeout a hung local runtime blocks preflight
    # and with it the whole chain, however small the configured reviewer timeout
    # is -- the one deadline the operator set does not reach this call (audit
    # 2026-08-30, HIGH). Loading a large model is slow, so the ceiling is generous
    # rather than tight; the point is that one exists.
    try:
        result = subprocess.run(
            ["lms", "load", model, "--context-length", str(context), "--yes"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=LMS_LOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, (f"`lms load {model}` did not finish within {LMS_LOAD_TIMEOUT}s "
                       f"— treating the runtime as unavailable rather than waiting.")
    except OSError as exc:
        return False, f"`lms load {model}` could not be started: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:300]
        return False, f"`lms load {model}` failed: {detail}"
    return True, f"{model} loaded (context {context})"


def preflight(p):
    """Cheap availability probe BEFORE burning a review round.

    Returns (ok, detail). Local endpoints: reachability only. Remote: auth
    probe via GET /models; OpenRouter additionally reports remaining credits
    (the only listed provider with a balance API — elsewhere exhaustion only
    shows on use, as 429/402, which the caller handles).
    """
    headers = auth_headers(p)
    host = urlsplit(p["base_url"]).hostname or ""

    loaded, detail = ensure_model_loaded(p)
    if not loaded:
        return False, detail

    try:
        http_get_json(p["base_url"] + "/models", headers)
    except (EgressDenied, AllowlistUnreadable) as exc:
        # Should not fire — profile() already vetted base_url — but a probe URL
        # that the allowlist rejects is a refusal, not an outage. Say which.
        return False, f"egress refused ({exc})"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, f"auth rejected (HTTP {exc.code})"
        if exc.code in (402, 429):
            return False, f"quota/payment exhausted (HTTP {exc.code})"
        if exc.code not in COMPAT_TOLERATED_CODES:
            # Everything else -- 500, 503, a gateway error -- means the provider is
            # not healthy. This used to fall through to "reachable is good enough",
            # which burned a review round against an endpoint that had already said
            # it was broken (audit 2026-08-30).
            return False, f"provider unhealthy (HTTP {exc.code})"
        # 404/405 only: some compat layers genuinely do not expose /models.
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"unreachable ({getattr(exc, 'reason', exc)})"

    if is_local(p):
        return True, "local endpoint (no credit check needed, nothing leaves the machine)"

    if "openrouter" in host:
        try:
            data = http_get_json(p["base_url"] + "/credits", headers).get("data", {})
            remaining = float(data.get("total_credits", 0)) - float(data.get("total_usage", 0))
            if remaining <= 0:
                return False, f"no credits left (remaining {remaining:.2f} USD)"
            return True, f"credits remaining: {remaining:.2f} USD"
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return True, "reachable; credit lookup failed (proceeding, 402/429 on use is terminal)"

    return True, "reachable + auth ok (no balance API — exhaustion only shows on use)"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# Files whose contents must never be inlined into a prompt bound for a remote
# model, whatever the caller asked for. --plan / --log / --system-file had no
# restriction at all: `.env`, a key file or unredacted scanner output could be
# selected and shipped (audit 2026-08-30, HIGH).
# Matched against the file's STEM as well as its full name, so `secrets.yaml`,
# `.env.production` and `credentials.json` are caught alongside bare `.env`.
SECRET_NAME_RE = re.compile(
    r"(^|[.\-_])(env|secrets?|credentials?|htpasswd|netrc|pgpass|keystore)($|[.\-_])"
    r"|\.(pem|key|p12|pfx|jks|kdbx|asc|ppk)$"
    r"|^(id_rsa|id_ed25519|id_ecdsa|\.npmrc|\.pypirc)",
    re.IGNORECASE,
)

MAX_INPUT_BYTES = 4 * 1024 * 1024


SCRATCH_DIR_ENV = "CLAUDEX_SCRATCH_DIR"


def _confined_roots():
    """Where a file that will be sent to a remote model may come from.

    The repo, because that is the work. `<repo>/.claudex-tmp/` and an explicitly
    named CLAUDEX_SCRATCH_DIR, because that is where the skills stage prompts.

    ⛔ NOT the whole OS temp dir, which is what this used to allow. On Linux and
    macOS that is `/tmp`: world-writable, and a path any local user can plant a
    file in. The skills' own rule already says "⛔ Never /tmp. Prefer the harness
    scratchpad; otherwise <repo>/.claudex-tmp/" -- this is that rule enforced
    instead of merely written down. (CodeRabbit, 2026-08-30.)
    """
    repo = Path(_repo_root() or os.getcwd()).resolve()
    roots = [repo, (repo / ".claudex-tmp")]
    named = os.environ.get(SCRATCH_DIR_ENV, "").strip()
    if named:
        roots.append(Path(named).expanduser().resolve())
    return roots


def read_confined(path, label):
    """Read a file the review will send onward -- or refuse, with the reason.

    Three refusals, in the order that gives the most useful message:

    1. outside the roots _confined_roots() allows -- the repo, `<repo>/.claudex-tmp/`
       and an explicitly named CLAUDEX_SCRATCH_DIR. NOT the OS temp dir, which this
       docstring claimed until CodeRabbit read it against the code (2026-08-30);
    2. a name that reads as a credential store;
    3. larger than MAX_INPUT_BYTES.

    The size cap is not politeness -- an oversized input is read whole into memory
    and then paid for at every provider in the chain.
    """
    resolved = Path(os.path.realpath(os.path.expanduser(str(path))))
    roots = _confined_roots()
    if not any(_is_within(resolved, root) for root in roots):
        listed = ", ".join(str(r) for r in roots)
        raise SystemExit(
            f"{label}: {resolved} is outside the allowed roots ({listed}).\n"
            f"  This file would be inlined into a prompt and sent to a remote model, "
            f"so it has to come from the work, not from anywhere on disk.\n"
            f"  Stage it in the repo, in <repo>/.claudex-tmp/, or in the directory "
            f"named by CLAUDEX_SCRATCH_DIR.")
    if any(SECRET_NAME_RE.search(part) for part in (resolved.name, resolved.stem)):
        raise SystemExit(
            f"{label}: refusing to inline {resolved.name} — that name reads as a "
            f"credential file, and this content goes to a remote model.")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise SystemExit(f"{label}: cannot read {resolved}: {exc}") from exc
    if size > MAX_INPUT_BYTES:
        raise SystemExit(
            f"{label}: {resolved} is {size} bytes, over the {MAX_INPUT_BYTES}-byte "
            f"ceiling. Trim it — every provider in the chain would be billed for it.")
    return read_text(resolved)


def _is_within(child, root):
    try:
        return os.path.commonpath([str(child), str(root)]) == str(root)
    except ValueError:
        return False


def read_text(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def strip_thinking(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# A finding says something. An outline entry ("1. Security", "2. Tests") does not,
# and counting those is how a three-line summary used to satisfy the anti-rubber-
# stamp floor -- the old comment called over-counting harmless, and it was not
# (audit 2026-08-30). Length is a crude proxy for substance, but it is the one
# that cannot be gamed by accident.
FINDING_RE = re.compile(r"^[ \t]*\d{1,2}[.)]\s+(\S.*?)(?=\n[ \t]*\d{1,2}[.)]\s+\S|\Z)", re.M | re.S)
MIN_FINDING_CHARS = 40


def count_findings(text):
    """Count numbered findings with enough substance to be one.

    Each entry runs to the NEXT numbered line, not to its own line ending. Real
    findings are multi-line -- an anchor, then the failure, then the fix -- and
    measuring only the first line dropped every one whose opening line was short
    ("1. **CRITICAL** -- `wrapper_guard.py:51`"). That undercounts, and the count
    feeds the anti-rubber-stamp floor: a thorough review could be rejected as a
    rubber stamp for formatting its findings the normal way. (CodeRabbit,
    2026-08-30.)

    Numbers are not de-duplicated: multi-section reports restart numbering per
    section, so `1.` may legitimately appear several times.
    """
    return sum(1 for body in FINDING_RE.findall(text) if len(body.strip()) >= MIN_FINDING_CHARS)


class ProviderError(Exception):
    """Transport/HTTP failure. terminal=True means: don't retry THIS provider."""

    def __init__(self, message, terminal=False):
        super().__init__(message)
        self.terminal = terminal


def run_review(p, args, plan_hash, user_content):
    """One review call against one provider. Returns (exit_code, verdict_line).

    Raises ProviderError on transport/HTTP failure so a chain can move on.
    """
    system_prompt = (
        read_confined(args.system_file, "--system-file") if args.system_file else SYSTEM_PROMPT
    )
    payload = json.dumps({
        "model": p["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": p["temperature"],
        "max_tokens": p["max_tokens"],
        "stream": False,
    }).encode("utf-8")

    chat_url = p["base_url"] + "/chat/completions"
    check_egress(chat_url)  # the request that actually carries the plan
    req = urllib.request.Request(chat_url, data=payload, headers=auth_headers(p))
    try:
        with _opener(chat_url).open(req, timeout=p["timeout"]) as resp:
            body = json.load(resp)
    except (EgressDenied, AllowlistUnreadable) as exc:
        # _RefuseRedirects raises EgressDenied from inside urlopen. Uncaught it
        # left run_review as a traceback instead of a recorded round, so the
        # chain could neither move on nor log what happened -- while preflight()
        # already handled the same two exceptions properly. Terminal: a provider
        # that redirects will redirect again. (CodeRabbit, 2026-08-30.)
        raise ProviderError(f"egress refused ({exc})", terminal=True) from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        # Quota/auth errors are terminal for this provider; 5xx may be transient.
        terminal = exc.code in (401, 402, 403, 404, 429)
        raise ProviderError(f"HTTP {exc.code} from {p['base_url']} — {detail}",
                            terminal=terminal) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ProviderError(f"endpoint unreachable or bad reply ({exc})",
                            terminal=False) from exc

    try:
        msg = body["choices"][0]["message"]
    except (KeyError, IndexError):
        raise ProviderError(f"unexpected response shape: {json.dumps(body)[:300]}",
                            terminal=True) from None
    critique = strip_thinking(msg.get("content") or "")
    if not critique and msg.get("reasoning_content"):
        critique = strip_thinking(msg["reasoning_content"])
    finish = body["choices"][0].get("finish_reason")
    if finish == "length":
        # Seen in practice: a dense local model re-listed the inlined diff with
        # line numbers and ran out of output budget before any verdict line.
        print(f"WARNING: {p['name']} hit max_tokens ({p['max_tokens']}) — the reply "
              "is truncated. Raise CLAUDEX_REVIEWER_<NAME>_MAX_TOKENS, shorten the "
              "input, or pick a model that doesn't echo the material; the verdict "
              "check below will (correctly) reject this round.")

    # Validate FIRST, write SECOND — the output file carries the verdict status
    # in its header, so a reader who ignores the exit code cannot mistake an
    # invalid review for a recorded round.
    code, status = validate(critique, args, p)
    header = (f"# Reviewer: {p['model']} via {p['name']} (fallback — not the primary reviewer)\n"
              f"# Round: {args.round_no} | Plan SHA256: {plan_hash}\n"
              f"# Status: {status}\n\n")
    output = header + critique + "\n"
    if args.out:
        write_atomically(args.out, output)
        print(f"Review written: {args.out}")
    else:
        print(output)
    print(f"{status} | findings: {count_findings(critique)} | plan-sha256: {plan_hash} "
          f"| reviewer: {p['name']}")
    # FALLBACK.md: "Every fallback round is recorded as: ## Round <n> — <model>
    # (via <reviewer>, fallback — plan-text only, no repo access)". Both halves of
    # that rule were broken: the log was optional, and the heading dropped the
    # limitation the rule exists to put in front of the reader (audit 2026-08-30).
    label = FALLBACK_LABEL if code == 0 else f"{FALLBACK_LABEL}, INVALID ATTEMPT, does not count as a round"
    entry = (f"\n## Round {args.round_no} — {p['model']} (via {p['name']}, {label})\n\n"
             f"_Status: {status} · plan SHA256 {plan_hash}_\n\n{critique}\n")
    with open(args.append_log, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(entry)
    print(f"Round appended to {args.append_log}")
    return code


def write_atomically(target, text):
    """Replace the target only once the whole reply is on disk.

    A failed round used to leave the PREVIOUS round's verdict sitting at --out,
    so a reader who checked the file rather than the exit code saw a stale
    approval for the current plan (audit 2026-08-30).
    """
    target = Path(target)
    staging = target.with_name(target.name + ".claudex-part")
    try:
        # `newline` on write_text needs Python 3.10, which is the declared floor.
        # It briefly was not: the floor said 3.8 while this call already used the
        # keyword, so the scripts would have died with a TypeError on the version
        # they promised. (CodeRabbit, 2026-08-30.)
        staging.write_text(text, encoding="utf-8", newline="\n")
        staging.replace(target)
    finally:
        if staging.exists():
            staging.unlink()


def validate(critique, args, p):
    """Apply the verdict grammar and the rubber-stamp gate.

    Returns (exit_code, status_line): 0 with the verdict summary, or 3 with an
    'INVALID: …' reason. Markdown decoration around a verdict line is
    tolerated (**VERDICT: X**, etc.).

    The verdict must be the LAST non-blank line, which is what the system prompt
    asks for ("End your reply with EXACTLY one line"). The parser used to accept
    the last match anywhere in the reply, so a model could write `VERDICT:
    APPROVED` mid-text and carry on -- the documented grammar was a suggestion
    rather than a boundary (audit 2026-08-30). With several named verdicts the
    requirement is that they occupy the closing lines, in any order.
    """
    DECOR = r"[\s*_`>#-]*"
    lines = [line for line in critique.splitlines() if line.strip()]
    # As many closing lines as there are verdicts to find, plus nothing else.
    expected = len(args.require_verdicts.split(",")) if args.require_verdicts else 1
    tail = "\n".join(lines[-expected:]) if lines else ""

    def last_verdict(name, values):
        rx = rf"^{DECOR}{re.escape(name)}:\s*({'|'.join(map(re.escape, values))})[\s*_`.!]*$"
        found = re.findall(rx, tail, flags=re.MULTILINE | re.IGNORECASE)
        return found[-1].upper() if found else None

    findings = count_findings(critique)
    if args.require_verdicts:
        # Custom grammar: every named verdict must appear; first value = pass.
        results, missing, all_pass = [], [], True
        for entry in args.require_verdicts.split(","):
            name, _, vals = entry.strip().partition(":")
            values = [v.strip() for v in vals.split("|") if v.strip()]
            got = last_verdict(name.strip(), values)
            if got is None:
                missing.append(name.strip())
                continue
            results.append(f"{name.strip().upper()}: {got}")
            if got != values[0].upper():
                all_pass = False
        if missing:
            return 3, (f"INVALID: missing verdict line(s) {missing} — "
                       "do not record this as a round")
        if args.round_no == 1 and all_pass and findings < args.min_findings:
            return 3, (f"INVALID: rubber-stamp suspicion — all verdicts passing in "
                       f"round 1 with only {findings} finding(s) (< {args.min_findings})")
        return 0, " | ".join(results)

    verdict = last_verdict("VERDICT", ["APPROVED", "REVISE"])
    if verdict is None:
        return 3, ("INVALID: no VERDICT line at the end of the reply — "
                   "do not record this as a round")
    if args.round_no == 1 and verdict == "APPROVED" and findings < args.min_findings:
        return 3, (f"INVALID: rubber-stamp suspicion — APPROVED in round 1 with only "
                   f"{findings} finding(s) (< {args.min_findings}); check the "
                   "model/prompt, or skip the review and log the plan as not cross-reviewed")
    return 0, f"VERDICT: {verdict}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", help="plan file (inlined into the prompt)")
    ap.add_argument("--log", help="review log so far (inlined; replaces the "
                                  "resumed-session memory the primary reviewer had)")
    ap.add_argument("--reviewer", help="profile name (default: first of CLAUDEX_REVIEWERS)")
    ap.add_argument("--chain", action="store_true",
                    help="walk CLAUDEX_REVIEWERS in order: preflight each, use the "
                         "first viable one, move on when a provider is out of "
                         "quota/credits — every skip is reported")
    ap.add_argument("--round", type=int, default=1, dest="round_no")
    ap.add_argument("--out", help="write the critique here (default: stdout)")
    ap.add_argument("--append-log",
                    help="REQUIRED for a review run. Appends this round to the review "
                         "log (e.g. PLAN-REVIEW-LOG.md) as '## Round <n> — <model> "
                         "(via <reviewer>, " + FALLBACK_LABEL + ")' with the status "
                         "line and the full critique — INVALID attempts are appended "
                         "too, labeled as not counting. Findings never live only in a "
                         "chat transcript.")
    ap.add_argument("--system-file",
                    help="file whose content replaces the built-in plan-review "
                         "system prompt — lets other gates (e.g. codex-verify) "
                         "reuse the transport/preflight/chain with their own prompt")
    ap.add_argument("--require-verdicts",
                    help="comma-list NAME:PASSVAL|OTHERVAL[|...] replacing the "
                         "default 'VERDICT: APPROVED|REVISE' grammar, e.g. "
                         "'DOD:COMPLETE|INCOMPLETE,QUALITY:ACCEPTABLE|REVISE,"
                         "SECURITY:PASS|FAIL'. Every named verdict line must "
                         "appear or the review is invalid (exit 3); the FIRST "
                         "value of each entry counts as its passing value for "
                         "the rubber-stamp gate")
    ap.add_argument("--min-findings", type=int,
                    default=int(os.environ.get("CLAUDEX_FALLBACK_MIN_FINDINGS", "0")),
                    help="optional rubber-stamp gate, off by default: with a "
                         "value > 0, a round-1 APPROVED carrying fewer numbered "
                         "findings is rejected as INVALID (exit 3). Worth "
                         "setting for small local models; zero findings is "
                         "otherwise a legitimate verdict")
    ap.add_argument("--list", action="store_true", help="list configured reviewers and exit")
    ap.add_argument("--check", action="store_true",
                    help="preflight all configured reviewers (reachability/auth/"
                         "credits where the provider exposes them) and exit")
    args = ap.parse_args()

    load_dotenv()

    if args.list or args.check:
        names = reviewer_names()
        if not names:
            print("No reviewers configured (CLAUDEX_REVIEWERS / CLAUDEX_REVIEWER_*_BASE_URL).")
            return 2
        worst = 0
        for n in names:
            try:
                p = profile(n)
            except SystemExit as exc:
                print(f"{n}: INCOMPLETE — {exc}")
                worst = 2
                continue
            key = "key set" if p["api_key"] else "no key (local endpoint?)"
            line = f"{n}: {p['model']} @ {p['base_url']} [{key}]"
            if args.check:
                ok, detail = preflight(p)
                line += f" -> {'OK' if ok else 'UNAVAILABLE'}: {detail}"
                if not ok:
                    worst = max(worst, 1)
            print(line)
        return worst if args.check else 0

    if not args.plan:
        ap.error("--plan is required (unless --list/--check)")
    if not args.append_log:
        # FALLBACK.md: "Findings never live only in the chat." The flag was
        # optional until 2026-08-30, so the documented rule held only for whoever
        # remembered to pass it.
        ap.error("--append-log is required: every fallback round is recorded, "
                 "including the invalid ones. Name the review log.")

    # Clear a previous round's verdict BEFORE anything can fail. Otherwise a run
    # that never reaches run_review() leaves a stale approval at --out for the
    # current plan.
    if args.out and os.path.exists(args.out):
        os.unlink(args.out)

    names = reviewer_names()
    if args.chain:
        candidates = names
    else:
        name = args.reviewer or (names[0] if names else None)
        candidates = [name] if name else []
    if not candidates:
        print("ERROR: No fallback reviewer configured. Set CLAUDEX_REVIEWERS and a "
              "CLAUDEX_REVIEWER_<NAME>_* profile (see .env.example).")
        return 2

    plan_hash = sha256_file(args.plan)
    # read_confined, not read_text: everything joined here is inlined into a
    # prompt bound for a remote model.
    user_parts = []
    if args.log:
        user_parts.append(
            "Review history so far (earlier findings and the planner's "
            "responses — check whether they are addressed, and find what is "
            "new):\n=== BEGIN LOG ===\n" + read_confined(args.log, "--log")
            + "\n=== END LOG ===\n")
    user_parts.append(
        f"Review round {args.round_no}. The plan under review "
        f"(SHA256 {plan_hash[:16]}):\n=== BEGIN PLAN ===\n"
        + read_confined(args.plan, "--plan") + "\n=== END PLAN ===")
    user_content = "\n".join(user_parts)

    for name in candidates:
        try:
            p = profile(name)
        except SystemExit as exc:
            if not args.chain:
                print(f"ERROR: {exc}")
                return 2  # config error — keep the documented exit-code contract
            print(f"CHAIN: skipping '{name}' — {exc}")
            continue

        ok, detail = preflight(p)
        if not ok and args.chain and detail.startswith("unreachable"):
            # Transient network stumble gets one retry before the chain moves on.
            print(f"CHAIN: preflight {name}: {detail} — retrying once ...")
            ok, detail = preflight(p)
        print(f"{'CHAIN: ' if args.chain else ''}preflight {name}: "
              f"{'OK' if ok else 'UNAVAILABLE'} — {detail}")
        if not ok:
            if not args.chain:
                print("ERROR: reviewer unavailable — pick another (--reviewer/--chain) "
                      "or wait/skip per FALLBACK.md.")
                return 2
            continue

        attempts = 2  # one retry for transient failures on this provider
        while attempts:
            attempts -= 1
            try:
                return run_review(p, args, plan_hash, user_content)
            except ProviderError as exc:
                kind = "terminal" if exc.terminal else "transient"
                print(f"{'CHAIN: ' if args.chain else 'ERROR: '}{name}: {exc} [{kind}]")
                if exc.terminal:
                    break
                if attempts:
                    print(f"{'CHAIN: ' if args.chain else ''}retrying {name} once ...")
        if not args.chain:
            return 2
        print(f"CHAIN: '{name}' failed — moving to the next configured reviewer.")

    if args.chain:
        print("ERROR: every configured reviewer failed preflight or errored. "
              "Remaining options per FALLBACK.md: wait for the Codex reset, or "
              "skip the review with an explicit log entry.")
    return 2


if __name__ == "__main__":
    sys.exit(main())

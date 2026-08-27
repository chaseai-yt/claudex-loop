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
2. Rubber-stamping does not count. A reply must end with a VERDICT line, and
   an APPROVED in round 1 with fewer than --min-findings numbered findings is
   treated as an INVALID review (exit 3), not as an approval.

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
        [--min-findings 3] [--list] [--check]

Exit codes: 0 = valid review with verdict; 2 = config/transport error (or, in
--chain mode, every configured provider failed preflight or errored); 3 =
invalid review (no verdict line, or rubber-stamp suspicion). Never treat exit
2/3 as an approval.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
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
    "than assuming it holds. End your reply with EXACTLY one line: "
    "'VERDICT: APPROVED' if the plan is sound enough to implement, or "
    "'VERDICT: REVISE' if it still has material problems."
)


def load_dotenv(path=".env"):
    """Minimal .env loader — process env wins over file values."""
    if not os.path.exists(path):
        return
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
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
        print(f"WARNING: {prefix}API_KEY holds the key inline — prefer "
              f"{prefix}API_KEY_ENV pointing at a variable your secret manager "
              "injects, so the key never sits in a file.")
    if not api_key and key_env:
        api_key = os.environ.get(key_env)
        if not api_key:
            raise SystemExit(
                f"Reviewer '{name}': {prefix}API_KEY_ENV points at '{key_env}' "
                "but that variable is empty. Refusing to call without the key.")
    base_url = base_url.rstrip("/")
    parts = urlsplit(base_url)
    if (api_key and parts.scheme == "http"
            and parts.hostname not in ("127.0.0.1", "localhost", "::1")):
        raise SystemExit(
            f"Reviewer '{name}': refusing to send a bearer key over plain http "
            f"to non-local host {parts.hostname}. Use https.")

    def num(suffix, default, cast):
        raw = get(suffix, default)
        try:
            return cast(raw)
        except (TypeError, ValueError):
            raise SystemExit(
                f"Reviewer '{name}': {prefix}{suffix}='{raw}' is not a number.")

    return {
        "name": name,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "temperature": num("TEMPERATURE", "0.7", float),
        "max_tokens": num("MAX_TOKENS", "8192", int),
        "timeout": num("TIMEOUT", "1800", int),
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


def is_local(p):
    host = urlsplit(p["base_url"]).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def http_get_json(url, headers, timeout=15):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def preflight(p):
    """Cheap availability probe BEFORE burning a review round.

    Returns (ok, detail). Local endpoints: reachability only. Remote: auth
    probe via GET /models; OpenRouter additionally reports remaining credits
    (the only listed provider with a balance API — elsewhere exhaustion only
    shows on use, as 429/402, which the caller handles).
    """
    headers = auth_headers(p)
    host = urlsplit(p["base_url"]).hostname or ""
    try:
        http_get_json(p["base_url"] + "/models", headers)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, f"auth rejected (HTTP {exc.code})"
        if exc.code in (402, 429):
            return False, f"quota/payment exhausted (HTTP {exc.code})"
        # Other codes (404/405 …): some compat layers don't expose /models —
        # reachable is good enough.
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


def read_text(path):
    with io.open(path, encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


def strip_thinking(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def count_findings(text):
    """Count numbered findings (lines starting '1.' / '2)' etc.)."""
    return len(set(re.findall(r"^\s*(\d{1,2})[.)]\s+\S", text, flags=re.M)))


class ProviderError(Exception):
    """Transport/HTTP failure. terminal=True means: don't retry THIS provider."""

    def __init__(self, message, terminal=False):
        super().__init__(message)
        self.terminal = terminal


def run_review(p, args, plan_hash, user_content):
    """One review call against one provider. Returns (exit_code, verdict_line).

    Raises ProviderError on transport/HTTP failure so a chain can move on.
    """
    system_prompt = read_text(args.system_file) if args.system_file else SYSTEM_PROMPT
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

    req = urllib.request.Request(p["base_url"] + "/chat/completions",
                                 data=payload, headers=auth_headers(p))
    try:
        with urllib.request.urlopen(req, timeout=p["timeout"]) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        # Quota/auth errors are terminal for this provider; 5xx may be transient.
        terminal = exc.code in (401, 402, 403, 404, 429)
        raise ProviderError(f"HTTP {exc.code} from {p['base_url']} — {detail}",
                            terminal=terminal)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ProviderError(f"endpoint unreachable or bad reply ({exc})", terminal=False)

    try:
        msg = body["choices"][0]["message"]
    except (KeyError, IndexError):
        raise ProviderError(f"unexpected response shape: {json.dumps(body)[:300]}",
                            terminal=True)
    critique = strip_thinking(msg.get("content") or "")
    if not critique and msg.get("reasoning_content"):
        critique = strip_thinking(msg["reasoning_content"])

    header = (f"# Reviewer: {p['model']} via {p['name']} (fallback — not the primary reviewer)\n"
              f"# Round: {args.round_no} | Plan SHA256: {plan_hash}\n\n")
    output = header + critique + "\n"
    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(output)
        print(f"Review written: {args.out}")
    else:
        print(output)

    # Markdown decoration around a verdict line is tolerated (**VERDICT: X**,
    # etc.); if a verdict appears several times, the LAST occurrence counts.
    DECOR = r"[\s*_`>#-]*"

    def last_verdict(name, values):
        rx = rf"^{DECOR}{re.escape(name)}:\s*({'|'.join(map(re.escape, values))})[\s*_`.!]*$"
        found = re.findall(rx, critique, flags=re.M | re.I)
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
            print(f"INVALID: missing verdict line(s) {missing} — "
                  "do not record this as a round.")
            return 3
        print(f"{' | '.join(results)} | findings: {findings} "
              f"| plan-sha256: {plan_hash} | reviewer: {p['name']}")
        if args.round_no == 1 and all_pass and findings < args.min_findings:
            print(f"INVALID: rubber-stamp suspicion — all verdicts passing in round 1 "
                  f"with only {findings} finding(s) (< {args.min_findings}).")
            return 3
        return 0

    verdict = last_verdict("VERDICT", ["APPROVED", "REVISE"])
    if verdict is None:
        print("INVALID: no VERDICT line at the end of the reply — "
              "do not record this as a round.")
        return 3
    print(f"VERDICT: {verdict} | findings: {findings} | plan-sha256: {plan_hash} "
          f"| reviewer: {p['name']}")
    if args.round_no == 1 and verdict == "APPROVED" and findings < args.min_findings:
        print(f"INVALID: rubber-stamp suspicion — APPROVED in round 1 with only "
              f"{findings} finding(s) (< {args.min_findings}). Check the model/prompt, "
              "or skip the review and log the plan as not cross-reviewed.")
        return 3
    return 0


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
                    default=int(os.environ.get("CLAUDEX_FALLBACK_MIN_FINDINGS", "3")),
                    help="round 1: an APPROVED with fewer numbered findings is "
                         "treated as a rubber stamp (exit 3)")
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
    user_parts = []
    if args.log:
        user_parts.append(
            "Review history so far (earlier findings and the planner's "
            "responses — check whether they are addressed, and find what is "
            "new):\n=== BEGIN LOG ===\n" + read_text(args.log) + "\n=== END LOG ===\n")
    user_parts.append(
        f"Review round {args.round_no}. The plan under review "
        f"(SHA256 {plan_hash[:16]}):\n=== BEGIN PLAN ===\n"
        + read_text(args.plan) + "\n=== END PLAN ===")
    user_content = "\n".join(user_parts)

    for name in candidates:
        try:
            p = profile(name)
        except SystemExit as exc:
            if not args.chain:
                raise
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

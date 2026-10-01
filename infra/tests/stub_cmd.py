#!/usr/bin/env python3
"""Generic stub command backing every fake binary in a `MakeSandbox` bin/.

`MakeSandbox` (see conftest.py) makes one executable copy of this file per
stubbed command name (`aws`, `docker`, `npm`, `curl`, `dig`, `sleep`, `uv`,
`uvx`, `gh`), each with an absolute `sys.executable` shebang written in
front of it. Every copy behaves identically; it only reacts differently
based on `os.path.basename(sys.argv[0])`, i.e. which name it was invoked
as.

On every invocation this script:

1. Appends one JSON line to the file named by the `STUB_LOG` environment
   variable (if set): `{"cmd": <basename>, "argv": [...], "env": {...}}`.
   `env` is the full environment the stub process received, so tests can
   assert on credential propagation.
2. Looks for a matching rule in the JSON array stored at the path named by
   the `STUB_RULES` environment variable. A rule matches this invocation
   when its own `cmd` is either absent or equal to this binary's basename,
   and every string in its `match` list is a substring of
   `" ".join(argv)`. Rules are tried in list order; the first match wins.
   `MakeSandbox.stub(...)` inserts new rules at the front, so the most
   recently registered rule takes priority.
3. If no rule matches, falls back to a fixed happy-path default (see
   `_default_rule`): `aws sts get-caller-identity` returns a fake identity;
   `aws lambda invoke ...` writes `null` to its output-file argument and
   prints `None` (a successful `FunctionError` query); everything else
   exits 0 with no output.
4. Prints the rule's `stdout`/`stderr` (if any), optionally writes
   `invoke_payload` (default `null`) to `output_file` (or, for `aws lambda
   invoke`, to the trailing positional argument, matching the real CLI's
   behavior of writing the invocation result there), then exits with the
   rule's `exit` code (default 0).
"""

import json
import os
import sys


def _default_identity_rule():
    return {
        "stdout": "123456789012\tarn:aws:sts::123456789012:assumed-role/dev/dev",
        "exit": 0,
    }


def _default_lambda_invoke_rule():
    return {"stdout": "None", "invoke_payload": "null", "exit": 0}


def _default_rule(cmd, args):
    if cmd == "aws" and len(args) >= 2 and args[0] == "sts" and args[1] == "get-caller-identity":
        return _default_identity_rule()
    if cmd == "aws" and len(args) >= 2 and args[0] == "lambda" and args[1] == "invoke":
        return _default_lambda_invoke_rule()
    return {"exit": 0}


def _load_rules(rules_path):
    if not rules_path or not os.path.exists(rules_path):
        return []
    with open(rules_path, encoding="utf-8") as fh:
        return json.load(fh)


def _find_rule(rules, cmd, args):
    joined = " ".join(args)
    for candidate in rules:
        candidate_cmd = candidate.get("cmd")
        if candidate_cmd not in (None, cmd):
            continue
        if all(token in joined for token in candidate.get("match", [])):
            return candidate
    return None


def _log_call(log_path, cmd, args):
    if not log_path:
        return
    record = {"cmd": cmd, "argv": args, "env": dict(os.environ)}
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _invoke_output_target(rule, cmd, args):
    output_file = rule.get("output_file")
    if output_file:
        return output_file
    if cmd == "aws" and len(args) >= 2 and args[0] == "lambda" and args[1] == "invoke" and args:
        last = args[-1]
        if not last.startswith("-"):
            return last
    return None


def main(argv):
    cmd = os.path.basename(argv[0])
    args = argv[1:]

    _log_call(os.environ.get("STUB_LOG"), cmd, args)

    rules = _load_rules(os.environ.get("STUB_RULES"))
    rule = _find_rule(rules, cmd, args) or _default_rule(cmd, args)

    stdout = rule.get("stdout", "")
    stderr = rule.get("stderr", "")
    if stdout:
        sys.stdout.write(stdout if stdout.endswith("\n") else stdout + "\n")
    if stderr:
        sys.stderr.write(stderr if stderr.endswith("\n") else stderr + "\n")

    target = _invoke_output_target(rule, cmd, args)
    if target:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(rule.get("invoke_payload", "null"))

    return int(rule.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main(sys.argv))

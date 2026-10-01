"""Shared fixtures for the infra test suite.

Fixtures
--------
`repo_root` -- `pathlib.Path` to the repository root.

`cfn_template(name)` -- loads `infra/<name>` through `cfn_yaml.load_path`
and returns the resulting dict (CFN intrinsics already in long form).

`sandbox` -- a fresh `MakeSandbox` (see below) rooted in a per-test
`tmp_path`.

MakeSandbox
-----------
Copies the repo's real `Makefile`, `.env.example` and `infra/` (templates
and scripts) into a temp dir, plus a minimal `back/` and
`front/dist/{index.html,assets/app.js}` so recipes that reference them
don't fail on missing files. It prepends a stub `bin/` (backed by
`stub_cmd.py`) for `aws docker npm curl dig sleep uv uvx gh` to `PATH`, so
no test ever calls a real `aws`/`docker`/etc binary.

Environment isolation (applied to every `sandbox.run`/`print_vars`/
`run_script` call): `PATH=<stub bin>:/usr/bin:/bin`, `HOME=<sandbox root>`,
and every `AWS_*`, `*_DOMAIN`, `DOMAIN`, `TAG`, `MAKEFLAGS`, `MAKELEVEL`,
`MFLAGS` variable from the ambient environment is stripped, so a
developer's real AWS session or `make`-recursion state can never leak into
a sandboxed run. `make` itself is resolved from `$MAKE_BIN` (default
`make`), so tests can be re-run with `MAKE_BIN=/usr/bin/make` or
`MAKE_BIN=gmake`.

Sandbox API:
- `sandbox.stub(cmd, match=[...], stdout="", stderr="", exit=0,
  output_file=None, invoke_payload=None)` -- registers a canned response.
  `cmd` is the stubbed binary's name (e.g. `"aws"`); the rule matches an
  invocation of that binary when every string in `match` is a substring of
  its space-joined argv. The most recently registered rule wins.
- `sandbox.write_env(text)` -- writes `text` to the sandbox's `.env`.
- `sandbox.run(*targets, vars=None, env=None, dry_run=False)` -- runs
  `make [-n] <targets> [VAR=value ...]` in the sandbox and returns a
  `subprocess.CompletedProcess` (`text=True`, stdout/stderr captured).
- `sandbox.calls(cmd=None)` -- returns the ordered list of logged stub
  invocations (each `{"cmd", "argv", "env"}`), optionally filtered to one
  stubbed binary.
- `sandbox.print_vars(*names)` -- writes a wrapper makefile (`include
  Makefile` plus a `_print` target that echoes `NAME=value` for each of
  `names`) and returns a `{name: value}` dict. This works around GNU make
  3.81 (macOS) not supporting `--eval`.
- `sandbox.run_script(relative_path, *args, env=None)` -- runs
  `bash <sandbox root>/<relative_path> <args...>` (e.g. for
  `infra/scripts/cert.sh`, which isn't invoked through `make`) with the
  same environment isolation as `run`, and returns a `CompletedProcess`.
- `sandbox.base_env()` -- the isolated environment dict used by the above,
  exposed for tests that want to add to it directly.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from cfn_yaml import load_path

REPO_ROOT = Path(__file__).resolve().parents[2]

STUB_BINARIES = ("aws", "docker", "npm", "curl", "dig", "sleep", "uv", "uvx", "gh")

# Exact-name environment variables stripped from every sandboxed run.
_STRIP_EXACT = {"DOMAIN", "TAG", "MAKEFLAGS", "MAKELEVEL", "MFLAGS"}


def _should_strip_env_var(key):
    if key in _STRIP_EXACT:
        return True
    if key.startswith("AWS_"):
        return True
    return bool(key.endswith("_DOMAIN"))


@pytest.fixture
def repo_root():
    return REPO_ROOT


@pytest.fixture
def cfn_template():
    def _load(name):
        return load_path(REPO_ROOT / "infra" / name)

    return _load


class MakeSandbox:
    """See module docstring for the full API."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        self.stub_log = tmp_path / "stub.log"
        self.stub_rules_path = tmp_path / "stub_rules.json"
        self._rules = []
        self._write_rules()

        shutil.copy(REPO_ROOT / "Makefile", self.root / "Makefile")
        shutil.copy(REPO_ROOT / ".env.example", self.root / ".env.example")
        shutil.copytree(REPO_ROOT / "infra", self.root / "infra")

        (self.root / "back").mkdir()
        dist_dir = self.root / "front" / "dist"
        (dist_dir / "assets").mkdir(parents=True)
        (dist_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (dist_dir / "assets" / "app.js").write_text("// stub build output\n", encoding="utf-8")

        stub_src = Path(__file__).resolve().parent / "stub_cmd.py"
        stub_body = stub_src.read_text(encoding="utf-8")
        # Drop the existing `#!/usr/bin/env python3` line; the sandbox needs an
        # absolute interpreter path so it works regardless of the caller's PATH.
        stub_body = stub_body.split("\n", 1)[1]
        shebang = f"#!{sys.executable}\n"
        for name in STUB_BINARIES:
            target = self.bin_dir / name
            target.write_text(shebang + stub_body, encoding="utf-8")
            mode = target.stat().st_mode
            target.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def _write_rules(self):
        self.stub_rules_path.write_text(json.dumps(self._rules), encoding="utf-8")

    def stub(self, cmd, match=None, stdout="", stderr="", exit=0, output_file=None, invoke_payload=None):
        rule = {
            "cmd": cmd,
            "match": list(match or []),
            "stdout": stdout,
            "stderr": stderr,
            "exit": exit,
        }
        if output_file is not None:
            rule["output_file"] = output_file
        if invoke_payload is not None:
            rule["invoke_payload"] = invoke_payload
        self._rules.insert(0, rule)
        self._write_rules()

    def write_env(self, text):
        (self.root / ".env").write_text(text, encoding="utf-8")

    def base_env(self):
        env = {k: v for k, v in os.environ.items() if not _should_strip_env_var(k)}
        env["PATH"] = f"{self.bin_dir}:/usr/bin:/bin"
        env["HOME"] = str(self.root)
        env["STUB_LOG"] = str(self.stub_log)
        env["STUB_RULES"] = str(self.stub_rules_path)
        return env

    def _make_bin(self):
        return os.environ.get("MAKE_BIN", "make")

    def run(self, *targets, vars=None, env=None, dry_run=False):
        cmd = [self._make_bin()]
        if dry_run:
            cmd.append("-n")
        cmd.extend(targets)
        for key, value in (vars or {}).items():
            cmd.append(f"{key}={value}")
        run_env = self.base_env()
        run_env.update(env or {})
        return subprocess.run(cmd, cwd=self.root, env=run_env, capture_output=True, text=True, timeout=60)

    def run_script(self, relative_path, *args, env=None):
        script = self.root / relative_path
        run_env = self.base_env()
        run_env.update(env or {})
        return subprocess.run(
            ["bash", str(script), *args],
            cwd=self.root,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def calls(self, cmd=None):
        if not self.stub_log.exists():
            return []
        records = []
        for line in self.stub_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if cmd is None or record["cmd"] == cmd:
                records.append(record)
        return records

    def print_vars(self, *names):
        wrapper = self.root / "Makefile.print_vars"
        lines = ["include Makefile", "", ".PHONY: _print", "_print:"]
        for name in names:
            lines.append(f'\t@echo "{name}=$({name})"')
        wrapper.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = subprocess.run(
            [self._make_bin(), "-f", str(wrapper), "_print"],
            cwd=self.root,
            env=self.base_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        values = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                values[key] = value
        return values


@pytest.fixture
def sandbox(tmp_path):
    return MakeSandbox(tmp_path)

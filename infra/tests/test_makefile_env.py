"""`.env` loading and AWS credential precedence in the `Makefile` (FR-19, AC-16).

Environment-supplied credentials (SSO profile, exported session, CI OIDC)
must always win over whatever `.env` holds, on both paths make uses to run
`aws`: recipe commands (exported make variables) and `$(shell ...)` lookups
(`LOAD_ENV`, which sources `.env` itself because `$(shell)` does not see
exported variables before GNU make 4.4). `.env` must never be created as a
side effect of `-include`; only `start`/`test-back` create it on purpose.

Run under both `MAKE_BIN=/usr/bin/make` (3.81) and GNU make 4.x.
"""

import re

import pytest

AWS_CRED_VARS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE")
ENV_CREDS = {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y", "AWS_SESSION_TOKEN": "z"}
# Short on purpose: TC-16.6 flags anything that looks like a real (16+ char) key.
STATIC_ENV_FILE = "POSTGRES_USER=meetings\nAWS_ACCESS_KEY_ID=AKIASTATIC\nAWS_SECRET_ACCESS_KEY=staticsecret\n"
EMPTY_KEYS_ENV_FILE = "POSTGRES_USER=meetings\nAWS_ACCESS_KEY_ID=\nAWS_SECRET_ACCESS_KEY=\n"
ENV_FILES = [EMPTY_KEYS_ENV_FILE, STATIC_ENV_FILE]
ENV_FILE_IDS = ["empty-keys", "static-keys"]


def _sts_calls(sandbox):
    return [c for c in sandbox.calls("aws") if c["argv"][:2] == ["sts", "get-caller-identity"]]


def _describe_stacks_calls(sandbox):
    return [c for c in sandbox.calls("aws") if c["argv"][:2] == ["cloudformation", "describe-stacks"]]


def _makefile_text(repo_root):
    return (repo_root / "Makefile").read_text(encoding="utf-8")


def _aws_targets(repo_root):
    return sorted(set(re.findall(r"^(aws-[A-Za-z0-9_.-]+):", _makefile_text(repo_root), re.M)))


# --- TC-8.10 / TC-8.11: `.env` is never created implicitly ---------------------------------


@pytest.mark.parametrize("targets", [("help",), ()], ids=["help", "bare-make"])
def test_help_and_bare_make_do_not_create_env(sandbox, targets):
    """TC-8.10: `make help` and bare `make` leave `.env` absent."""
    result = sandbox.run(*targets)

    assert result.returncode == 0, result.stderr
    assert "Usage: make" in result.stdout
    assert not (sandbox.root / ".env").exists(), ".env must not be created by `make` / `make help`"


def test_include_is_guarded_by_wildcard(repo_root, sandbox):
    """TC-8.11: `-include $(wildcard .env)` is used, never a bare `-include .env`."""
    text = _makefile_text(repo_root)

    assert re.search(r"^-include \$\(wildcard \.env\)\s*$", text, re.M)
    assert not re.search(r"^-?include\s+\.env\s*$", text, re.M), "bare `-include .env` remakes .env"

    result = sandbox.run("help")
    assert result.returncode == 0, result.stderr
    assert not (sandbox.root / ".env").exists()


def test_env_rule_is_prerequisite_only_of_start_and_test_back(repo_root):
    """TC-8.13 (static): only `start` and `test-back` list `.env` as a prerequisite."""
    owners = set()
    for line in _makefile_text(repo_root).splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+(?:\s+[A-Za-z0-9_.-]+)*)\s*:(?!=)([^#]*)", line)
        if not match or line.startswith("\t"):
            continue
        prereqs = match.group(2).split()
        if ".env" in prereqs:
            owners.update(match.group(1).split())

    assert owners == {"start", "test-back"}


def test_aws_targets_never_create_env(repo_root, sandbox):
    """TC-8.13 (dynamic): no `aws-*` target (dry run) creates `.env` or plans to."""
    targets = _aws_targets(repo_root)
    assert "aws-check" in targets and "aws-deploy" in targets

    for target in targets:
        result = sandbox.run(target, dry_run=True)
        assert "cp .env.example .env" not in result.stdout, f"{target} would create .env"
        assert not (sandbox.root / ".env").exists(), f"`make -n {target}` created .env"


@pytest.mark.parametrize("target", ["aws-check", "aws-frontend-stack", "aws-backend-outputs"])
def test_aws_targets_run_without_creating_env(sandbox, target):
    """`.env` stays absent after real (stubbed) runs of `aws-*` targets."""
    result = sandbox.run(target)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (sandbox.root / ".env").exists()


def test_test_back_creates_env_and_uses_default_db_credentials(sandbox):
    """Regression: on a fresh clone `make test-back` creates `.env` on purpose and the
    test database URL still carries the default compose credentials, even though make
    no longer re-reads a `.env` created mid-run."""
    result = sandbox.run("test-back")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (sandbox.root / ".env").exists(), "test-back must still create .env from .env.example"
    uv_calls = [c for c in sandbox.calls("uv") if "pytest" in c["argv"]]
    assert uv_calls, "expected `uv run pytest`"
    assert "meetings:meetings@localhost:5432/meetings_test" in uv_calls[-1]["env"]["TEST_DATABASE_URL"]
    psql_calls = [c for c in sandbox.calls("docker") if "psql" in c["argv"]]
    assert psql_calls and all("-U" in c["argv"] and "meetings" in c["argv"] for c in psql_calls)


# --- TC-8.8: environment credentials beat `.env` ---------------------------------------------


@pytest.mark.parametrize("env_file", ENV_FILES, ids=ENV_FILE_IDS)
def test_env_credentials_win_on_recipe_path(sandbox, env_file):
    """TC-8.8a: `.env` keys (empty or static) never clobber exported credentials in recipes."""
    sandbox.write_env(env_file)

    result = sandbox.run("aws-check", env=ENV_CREDS)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = _sts_calls(sandbox)
    assert calls, "expected an aws sts get-caller-identity call"
    for key, value in ENV_CREDS.items():
        assert calls[-1]["env"].get(key) == value


@pytest.mark.parametrize("env_file", ENV_FILES, ids=ENV_FILE_IDS)
def test_env_credentials_win_on_shell_path(sandbox, env_file):
    """TC-8.8b: the `$(shell)` lookups (LOAD_ENV) keep the exported credentials too."""
    sandbox.write_env(env_file)

    sandbox.run("aws-backend-health", env=ENV_CREDS, dry_run=True)

    calls = _describe_stacks_calls(sandbox)
    assert calls, "expected a describe-stacks lookup from $(shell)"
    for call in calls:
        for key, value in ENV_CREDS.items():
            assert call["env"].get(key) == value
        assert call["env"].get("POSTGRES_USER") == "meetings", "non-credential .env keys still load"


def test_env_profile_suppresses_all_env_file_keys(sandbox):
    """TC-8.8c: the four credential variables form a group: an env `AWS_PROFILE` alone is
    enough to keep `.env` static keys out of both the recipe and the `$(shell)` path."""
    sandbox.write_env(STATIC_ENV_FILE)
    env = {"AWS_PROFILE": "sso"}

    result = sandbox.run("aws-check", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    sandbox.run("aws-backend-health", env=env, dry_run=True)

    calls = _sts_calls(sandbox) + _describe_stacks_calls(sandbox)
    assert _sts_calls(sandbox) and _describe_stacks_calls(sandbox)
    for call in calls:
        assert call["env"].get("AWS_PROFILE") == "sso"
        assert "AWS_ACCESS_KEY_ID" not in call["env"]
        assert "AWS_SECRET_ACCESS_KEY" not in call["env"]


def test_static_env_keys_reach_shell_path_without_env_credentials(sandbox):
    """UC-8-A1: with no environment credentials, `.env` static keys still reach `$(shell)`."""
    sandbox.write_env(STATIC_ENV_FILE)

    sandbox.run("aws-backend-health", dry_run=True)

    calls = _describe_stacks_calls(sandbox)
    assert calls
    assert calls[-1]["env"].get("AWS_ACCESS_KEY_ID") == "AKIASTATIC"
    assert calls[-1]["env"].get("AWS_SECRET_ACCESS_KEY") == "staticsecret"


def test_no_credentials_anywhere_exports_none(sandbox):
    """No `.env` and no environment credentials: nothing (not even empty) is exported."""
    result = sandbox.run("aws-check")

    assert result.returncode == 0, result.stderr
    call_env = _sts_calls(sandbox)[-1]["env"]
    for key in AWS_CRED_VARS:
        assert key not in call_env


# --- TC-8.7: expired session ------------------------------------------------------------------


def test_expired_session_fails_aws_check_clearly(sandbox):
    """TC-8.7: an expired-token error fails `aws-check` with a message mentioning expiry."""
    sandbox.stub(
        "aws",
        match=["sts", "get-caller-identity"],
        stderr="An error occurred (ExpiredToken) when calling the GetCallerIdentity operation",
        exit=255,
    )

    result = sandbox.run("aws-check", env={"AWS_PROFILE": "sso"})

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "expired" in output.lower()
    assert "aws sso login" in output

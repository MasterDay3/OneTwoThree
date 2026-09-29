"""Slice 6: the deploy contract (`deploy-backend`, `deploy-frontend`) that CI runs verbatim.

TC-8.1, 8.2, 8.4, 8.5, 8.6, 8.9, 8.13 (dynamic), 9.6, 9.7, 9.8, 11.5.
"""

import re
import subprocess

ECR = "123456789012.dkr.ecr.us-east-1.amazonaws.com/meetings-backend"
FUNCTION_URL = "https://abc123.lambda-url.us-east-1.on.aws/"


def _happy_path(sandbox):
    sandbox.stub("aws", match=["describe-stacks", "meetings-backend-ecr"], stdout=ECR)
    sandbox.stub("aws", match=["describe-stacks", "meetings-backend", "ApiUrl"], stdout=FUNCTION_URL)
    sandbox.stub("aws", match=["describe-stacks", "meetings-frontend", "BucketName"], stdout="site-bucket")
    sandbox.stub("aws", match=["describe-stacks", "meetings-frontend", "DistributionId"], stdout="E123")
    sandbox.stub("aws", match=["describe-stacks", "meetings-cognito", "UserPoolId"], stdout="us-east-1_pool")


def _cfn_mutations(sandbox):
    return [
        c
        for c in sandbox.calls("aws")
        if c["argv"][:1] == ["cloudformation"] and c["argv"][1:2] != ["describe-stacks"]
    ]


def _index(calls, predicate):
    return next(i for i, c in enumerate(calls) if predicate(c["argv"]))


def test_deploy_backend_dry_run_order_and_no_cloudformation(sandbox):
    """TC-8.1: push → update-function-code → wait → migrate; never a stack deploy."""
    _happy_path(sandbox)
    out = sandbox.run("deploy-backend", vars={"TAG": "abc"}, dry_run=True).stdout

    # Each step must appear after the previous one (a wait also precedes the update, for in-flight updates).
    pos = 0
    steps = ["get-caller-identity", "buildx build", "update-function-code", "function-updated-v2", "migrate"]
    for step in steps:
        pos = out.index(step, pos)
    assert "cloudformation deploy" not in out


def test_deploy_backend_real_run_never_touches_cloudformation(sandbox):
    """TC-8.1 (real run): the stub log has no stack create/update/deploy call."""
    _happy_path(sandbox)
    result = sandbox.run("deploy-backend", vars={"TAG": "abc"})

    assert result.returncode == 0, result.stderr
    assert not _cfn_mutations(sandbox)
    update = [c for c in sandbox.calls("aws") if "update-function-code" in c["argv"]]
    assert update and f"{ECR}:abc" in update[0]["argv"]


def test_deploy_frontend_never_touches_cloudformation(sandbox):
    _happy_path(sandbox)
    assert "cloudformation deploy" not in sandbox.run("deploy-frontend", dry_run=True).stdout
    result = sandbox.run("deploy-frontend")

    assert result.returncode == 0, result.stderr
    assert not _cfn_mutations(sandbox)


def test_frontend_upload_order_is_safe(sandbox):
    """TC-8.2: assets without --delete → index.html no-cache → sync --delete → invalidation."""
    _happy_path(sandbox)
    assert sandbox.run("deploy-frontend").returncode == 0
    calls = [c for c in sandbox.calls("aws") if c["argv"][:1] in (["s3"], ["cloudfront"])]

    first_sync = _index(calls, lambda a: a[:2] == ["s3", "sync"])
    index_cp = _index(calls, lambda a: a[:2] == ["s3", "cp"] and "index.html" in " ".join(a))
    delete_sync = _index(calls, lambda a: a[:2] == ["s3", "sync"] and "--delete" in a)
    invalidation = _index(calls, lambda a: "create-invalidation" in a)

    assert "--delete" not in calls[first_sync]["argv"]
    assert "index.html" in calls[first_sync]["argv"]  # excluded
    assert "no-cache" in calls[index_cp]["argv"]
    assert "index.html" in calls[delete_sync]["argv"]  # excluded from the delete pass too
    assert first_sync < index_cp < delete_sync < invalidation


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True
    )


def test_tag_defaults_to_full_sha_and_unique_dirty_suffix(sandbox):
    """TC-8.4: clean tree → full SHA; dirty tree → <sha>-dirty-<timestamp>, unique per build."""
    _git(sandbox.root, "init", "-q")
    _git(sandbox.root, "add", "Makefile")
    _git(sandbox.root, "commit", "-qm", "init")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=sandbox.root, capture_output=True, text=True
    ).stdout.strip()

    assert sandbox.print_vars("TAG")["TAG"] == sha
    assert re.fullmatch(r"[0-9a-f]{40}", sha)

    (sandbox.root / "Makefile").write_text((sandbox.root / "Makefile").read_text() + "\n")
    first = sandbox.print_vars("TAG")["TAG"]
    subprocess.run(["sleep", "1"])
    second = sandbox.print_vars("TAG")["TAG"]
    assert re.fullmatch(rf"{sha}-dirty-\d{{14}}", first)
    assert first != second


def test_tag_outside_git_is_a_timestamp(sandbox):
    """TC-8.5: no git repo → the tag is a timestamp."""
    assert re.fullmatch(r"\d{14}", sandbox.print_vars("TAG")["TAG"])


def test_frontend_api_url_prefers_backend_domain(sandbox):
    """TC-8.6: VITE_API_URL is the backend-domain ApiUrl when that stack exists, else the function URL."""
    _happy_path(sandbox)
    assert sandbox.run("deploy-frontend").returncode == 0
    assert sandbox.calls("npm")[-1]["env"]["VITE_API_URL"] == FUNCTION_URL

    sandbox.stub(
        "aws", match=["describe-stacks", "meetings-backend-domain", "ApiUrl"], stdout="https://api.ex.com/"
    )
    assert sandbox.run("deploy-frontend").returncode == 0
    assert sandbox.calls("npm")[-1]["env"]["VITE_API_URL"] == "https://api.ex.com/"


def test_deploy_backend_is_rerunnable_and_never_creates_env(sandbox):
    """TC-8.9 + TC-8.13 (dynamic)."""
    _happy_path(sandbox)
    for _ in range(2):
        assert sandbox.run("deploy-backend", vars={"TAG": "fixed"}).returncode == 0
    assert sandbox.run("deploy-frontend").returncode == 0
    assert not (sandbox.root / ".env").exists()


def test_failed_migration_fails_the_deploy(sandbox):
    """TC-9.6: migrate returns FunctionError → non-zero after the code was updated."""
    _happy_path(sandbox)
    sandbox.stub("aws", match=["lambda", "invoke"], stdout="Unhandled")

    result = sandbox.run("deploy-backend", vars={"TAG": "abc"})

    assert result.returncode != 0
    assert any("update-function-code" in c["argv"] for c in sandbox.calls("aws"))


def test_failed_push_stops_before_lambda_update(sandbox):
    """TC-9.7: docker push fails → no update-function-code, no migrate."""
    _happy_path(sandbox)
    sandbox.stub("docker", match=["buildx"], exit=1)

    assert sandbox.run("deploy-backend", vars={"TAG": "abc"}).returncode != 0
    argvs = [" ".join(c["argv"]) for c in sandbox.calls("aws")]
    assert not any("update-function-code" in a or "invoke" in a for a in argvs)


def test_failed_invalidation_fails_the_deploy(sandbox):
    """TC-9.8."""
    _happy_path(sandbox)
    sandbox.stub("aws", match=["create-invalidation"], exit=1)

    assert sandbox.run("deploy-frontend").returncode != 0
    assert any(c["argv"][:2] == ["s3", "sync"] for c in sandbox.calls("aws"))


def test_push_never_tags_latest(sandbox):
    """TC-11.5 / FR-13: the image is tagged with TAG only."""
    _happy_path(sandbox)
    assert sandbox.run("aws-backend-push", vars={"TAG": "abc"}).returncode == 0
    build = [c for c in sandbox.calls("docker") if "buildx" in c["argv"]][0]["argv"]
    assert f"{ECR}:abc" in build
    assert not any(a.endswith(":latest") for a in build)

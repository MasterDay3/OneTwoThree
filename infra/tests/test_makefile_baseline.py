"""Characterization tests pinning down today's `Makefile` behavior, so
Slices 3/6/7 cannot regress it. Run under both `MAKE_BIN=/usr/bin/make`
(macOS, GNU make 3.81) and `MAKE_BIN=gmake`/GNU make 4.x where available.
"""

import re
import shutil


def test_existing_env_not_overwritten_by_start(sandbox):
    """TC-1.3: an existing `.env` is untouched by `make start` (docker stubbed)."""
    sandbox.write_env("DB_PORT=15432\nPOSTGRES_USER=meetings\n")
    before = (sandbox.root / ".env").read_text()

    result = sandbox.run("start")

    assert result.returncode == 0, result.stderr
    after = (sandbox.root / ".env").read_text()
    assert after == before, "make start must not modify an existing .env"
    assert any(c["cmd"] == "docker" for c in sandbox.calls()), "expected docker compose to be invoked"


def test_aws_deploy_order(sandbox):
    """TC-5.2: `make -n aws-deploy` runs cognito, then backend, then frontend."""
    result = sandbox.run("aws-deploy", dry_run=True)

    assert result.returncode == 0, result.stderr
    output = result.stdout
    idx_cognito = output.index("infra/cognito.yaml")
    idx_backend_ecr = output.index("infra/backend-ecr.yaml")
    idx_backend = output.index("infra/backend.yaml")
    idx_frontend = output.index("infra/frontend.yaml")

    assert idx_cognito < idx_backend_ecr < idx_backend < idx_frontend


def test_backend_stack_guarded_on_missing_cognito(sandbox):
    """TC-5.4: `aws-backend-stack` exits before any `cloudformation deploy` call
    when Cognito isn't deployed (its pool id resolves empty)."""
    sandbox.stub(
        "aws",
        match=["describe-stacks", "meetings-backend-ecr"],
        stdout="123456789012.dkr.ecr.us-east-1.amazonaws.com/meetings-backend",
    )

    result = sandbox.run("aws-backend-stack")

    assert result.returncode != 0
    assert "Cognito not deployed" in result.stdout + result.stderr
    deploy_calls = [
        c for c in sandbox.calls("aws") if "deploy" in c["argv"] and "cloudformation" in c["argv"]
    ]
    assert not deploy_calls, "no cloudformation deploy call should happen before the Cognito guard"


def test_frontend_publish_guarded_on_missing_backend(sandbox):
    """TC-5.5: `aws-frontend-publish` exits with "Backend not deployed" before
    any build/upload call when the backend stack's ApiUrl output is empty."""
    result = sandbox.run("aws-frontend-publish")

    assert result.returncode != 0
    assert "Backend not deployed" in result.stdout + result.stderr
    assert not sandbox.calls("npm"), "npm must never run before the backend guard"
    s3_calls = [c for c in sandbox.calls("aws") if c["argv"][:1] == ["s3"]]
    assert not s3_calls, "no s3 call should happen before the backend guard"


def test_failed_stack_cleanup_applies_to_every_stack_target(repo_root):
    """TC-5.6: every `*-stack`/`-ecr` recipe calls the same clear_failed_stack helper."""
    text = (repo_root / "Makefile").read_text(encoding="utf-8")
    targets = ["aws-backend-ecr", "aws-backend-stack", "aws-frontend-stack", "aws-cognito-stack"]

    for target in targets:
        pattern = rf"^{re.escape(target)}:.*\n(?:\t.*\n?)+"
        match = re.search(pattern, text, re.M)
        assert match, f"could not find the recipe for {target} in Makefile"
        assert "clear_failed_stack" in match.group(0), f"{target} does not call clear_failed_stack"


def test_arch_amd64_produces_platform_linux_amd64(sandbox):
    """TC-5.8: `ARCH=amd64` builds for linux/amd64, not the default arm64."""
    sandbox.stub(
        "aws",
        match=["describe-stacks", "meetings-backend-ecr"],
        stdout="123456789012.dkr.ecr.us-east-1.amazonaws.com/meetings-backend",
    )

    result = sandbox.run("aws-backend-push", vars={"ARCH": "amd64"})

    assert result.returncode == 0, result.stderr
    build_calls = [c for c in sandbox.calls("docker") if "buildx" in c["argv"] and "build" in c["argv"]]
    assert build_calls, "expected a docker buildx build call"
    assert any("linux/amd64" in c["argv"] for c in build_calls)
    assert not any("linux/arm64" in c["argv"] for c in build_calls)


def test_static_env_credentials_reach_aws_stub(sandbox):
    """TC-8.3: legacy static `.env` AWS keys still reach the aws stub."""
    sandbox.write_env("AWS_ACCESS_KEY_ID=AKIAFAKE\nAWS_SECRET_ACCESS_KEY=fakesecret\n")

    result = sandbox.run("aws-check")

    assert result.returncode == 0, result.stderr
    sts_calls = [c for c in sandbox.calls("aws") if c["argv"][:2] == ["sts", "get-caller-identity"]]
    assert sts_calls, "expected an aws sts get-caller-identity call"
    call_env = sts_calls[-1]["env"]
    assert call_env.get("AWS_ACCESS_KEY_ID") == "AKIAFAKE"
    assert call_env.get("AWS_SECRET_ACCESS_KEY") == "fakesecret"


def test_cors_origins_retains_cloudfront_and_custom_domain(sandbox):
    """TC-13.2: CORS_ORIGINS_AWS keeps both the CloudFront origin and the
    custom domain, via print_vars with a stubbed SiteOrigins output."""
    sandbox.stub(
        "aws",
        match=["describe-stacks", "meetings-frontend", "SiteOrigins"],
        stdout="https://d111111abcdef8.cloudfront.net,https://app.example.com",
    )

    values = sandbox.print_vars("CORS_ORIGINS_AWS")

    origins = values["CORS_ORIGINS_AWS"]
    assert "http://localhost:3000" in origins
    assert "http://localhost:5173" in origins
    assert "https://d111111abcdef8.cloudfront.net" in origins
    assert "https://app.example.com" in origins


def test_aws_binary_resolves_to_the_stub(sandbox):
    """Isolation test: shutil.which("aws", path=sandbox PATH) is the stub."""
    path = sandbox.base_env()["PATH"]
    resolved = shutil.which("aws", path=path)
    assert resolved == str(sandbox.bin_dir / "aws")

"""Slice 7: rollback, KEEP_IMAGE, backend custom domain, OIDC bootstrap, destroy order.

TC-11.1, 11.3, 11.4, 5.9 (AC-17), 6.9 (backend half), 7.4, 7.7, 4.3, 4.5, 4.8, 12.1, 12.9.
"""

import re

ECR = "123456789012.dkr.ecr.us-east-1.amazonaws.com/meetings-backend"
CERT = "arn:aws:acm:us-east-1:123456789012:certificate/abc"


def _ecr(sandbox):
    sandbox.stub("aws", match=["describe-stacks", "meetings-backend-ecr"], stdout=ECR)


def _argvs(sandbox, cmd="aws"):
    return [" ".join(c["argv"]) for c in sandbox.calls(cmd)]


# ---- rollback ----


def test_rollback_checks_ecr_then_updates_without_build_or_migrate(sandbox):
    """TC-11.1 / TC-11.4."""
    _ecr(sandbox)
    result = sandbox.run("aws-backend-rollback", vars={"TAG": "oldsha"})

    assert result.returncode == 0, result.stderr
    calls = _argvs(sandbox)
    describe = next(i for i, a in enumerate(calls) if "describe-images" in a and "imageTag=oldsha" in a)
    update = next(i for i, a in enumerate(calls) if "update-function-code" in a)
    assert describe < update
    assert f"{ECR}:oldsha" in calls[update]
    assert not sandbox.calls("docker")
    assert not any("invoke" in a and "migrate" in a for a in calls)


def test_rollback_fails_when_tag_is_not_in_ecr(sandbox):
    """TC-11.3."""
    _ecr(sandbox)
    sandbox.stub("aws", match=["describe-images"], exit=254)

    result = sandbox.run("aws-backend-rollback", vars={"TAG": "gone"})

    assert result.returncode != 0
    assert "not in ECR" in result.stdout + result.stderr
    assert not any("update-function-code" in a for a in _argvs(sandbox))


def test_rollback_requires_an_explicit_tag(sandbox):
    """The default (HEAD) tag can never be a rollback target."""
    result = sandbox.run("aws-backend-rollback")

    assert result.returncode != 0
    assert "TAG=" in result.stdout + result.stderr
    assert not sandbox.calls("aws")


def test_rollback_recipe_has_no_migration(repo_root):
    text = (repo_root / "Makefile").read_text()
    recipe = re.search(r"^aws-backend-rollback:.*\n(?:\t.*\n?)+", text, re.M).group(0)
    assert "migrate" not in recipe and "buildx" not in recipe


# ---- KEEP_IMAGE ----


def _backend_stack_stubs(sandbox):
    _ecr(sandbox)
    sandbox.stub("aws", match=["describe-stacks", "meetings-cognito", "UserPoolId"], stdout="us-east-1_pool")
    sandbox.stub("aws", match=["describe-stacks", "meetings-cognito", "Issuer"], stdout="https://issuer")


def test_keep_image_passes_the_live_image_uri(sandbox):
    """TC-5.9 / AC-17: a stack update never rolls the function back to a stale image."""
    _backend_stack_stubs(sandbox)
    sandbox.stub("aws", match=["lambda", "get-function"], stdout=f"{ECR}:live")

    result = sandbox.run("aws-backend-stack", vars={"KEEP_IMAGE": "1", "TAG": "other"})

    assert result.returncode == 0, result.stderr
    deploy = next(a for a in _argvs(sandbox) if a.startswith("cloudformation deploy"))
    assert f"ImageUri={ECR}:live" in deploy
    assert ":other" not in deploy


def test_keep_image_fails_without_a_live_function(sandbox):
    _backend_stack_stubs(sandbox)
    sandbox.stub("aws", match=["lambda", "get-function"], stdout="None")

    assert sandbox.run("aws-backend-stack", vars={"KEEP_IMAGE": "1"}).returncode != 0
    assert not any(a.startswith("cloudformation deploy") for a in _argvs(sandbox))


# ---- backend custom domain ----


def test_backend_domain_targets_fail_fast_without_domain(sandbox):
    """TC-6.9 (backend half): no AWS call at all."""
    for target in [
        "aws-backend-cert",
        "aws-backend-https",
        "aws-backend-cert-status",
        "aws-backend-https-check",
    ]:
        result = sandbox.run(target)
        assert result.returncode != 0, target
        assert "BACKEND_DOMAIN is empty" in result.stderr
    assert not sandbox.calls("aws")


def test_backend_https_deploys_the_domain_stack_then_republishes(sandbox):
    _ecr(sandbox)
    sandbox.stub("aws", match=["acm", "list-certificates"], stdout=CERT)
    sandbox.stub(
        "aws", match=["describe-stacks", "meetings-backend-domain", "ApiUrl"], stdout="https://api.ex.com/"
    )

    dry = sandbox.run("aws-backend-https", vars={"DOMAIN": "ex.com"}, dry_run=True).stdout
    assert "infra/backend-domain.yaml" in dry
    assert "BackendFunctionName=meetings-backend" in dry
    assert "DomainName=api.ex.com" in dry
    assert "aws-frontend-publish" in dry
    assert "infra/frontend.yaml" not in dry  # TC-7.7: never touches the frontend stack


def test_backend_https_check_prints_dns_and_status_then_fails(sandbox):
    """TC-7.4: both lines are printed before the non-zero exit."""
    sandbox.stub("curl", match=["api.ex.com"], stdout="000", exit=6)

    result = sandbox.run("aws-backend-https-check", vars={"DOMAIN": "ex.com"})

    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "DNS:" in out and "HTTP" in out and "propagat" in out


# ---- OIDC ----


def _oidc_stubs(sandbox, managed="false"):
    sandbox.stub("aws", match=["describe-stacks", "meetings-frontend", "BucketName"], stdout="site-bucket")
    sandbox.stub("aws", match=["describe-stacks", "meetings-frontend", "DistributionId"], stdout="E123")
    sandbox.stub(
        "aws", match=["describe-stacks", "meetings-github-oidc", "ProviderManagedByStack"], stdout=managed
    )
    sandbox.stub(
        "aws",
        match=["list-open-id-connect-providers"],
        stdout="arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
    )


def test_oidc_deploy_reuses_an_existing_provider(sandbox):
    """TC-4.3."""
    _oidc_stubs(sandbox)
    result = sandbox.run("aws-oidc-deploy")

    assert result.returncode == 0, result.stderr
    deploy = next(a for a in _argvs(sandbox) if a.startswith("cloudformation deploy"))
    assert "infra/github-oidc.yaml" in deploy
    assert "CAPABILITY_NAMED_IAM" in deploy and "--no-fail-on-empty-changeset" in deploy
    assert "ExistingOidcProviderArn=arn:aws:iam::123456789012:oidc-provider/" in deploy
    assert "FrontendBucketName=site-bucket" in deploy and "FrontendDistributionId=E123" in deploy
    assert "gh variable set AWS_DEPLOY_ROLE_ARN --repo MasterDay3/OneTwoThree" in result.stdout
    assert not sandbox.calls("gh"), "the gh command is printed, never executed"


def test_oidc_deploy_keeps_a_provider_it_manages(sandbox):
    """TC-4.8: the stack's own provider must not flip the condition on re-run."""
    _oidc_stubs(sandbox, managed="true")
    assert sandbox.run("aws-oidc-deploy").returncode == 0
    deploy = next(a for a in _argvs(sandbox) if a.startswith("cloudformation deploy"))
    assert re.search(r"ExistingOidcProviderArn=(\s|$)", deploy)


def test_oidc_deploy_needs_the_frontend_stack(sandbox):
    assert sandbox.run("aws-oidc-deploy").returncode != 0
    assert not any(a.startswith("cloudformation deploy") for a in _argvs(sandbox))


def test_oidc_deploy_clears_failed_stacks(repo_root):
    """TC-4.5."""
    text = (repo_root / "Makefile").read_text()
    recipe = re.search(r"^aws-oidc-deploy:.*\n(?:\t.*\n?)+", text, re.M).group(0)
    assert "clear_failed_stack" in recipe


# ---- destroy order ----


def _destroy_order(sandbox, **vars):
    out = sandbox.run("aws-destroy", vars=vars, dry_run=True).stdout
    names = ["meetings-frontend", "meetings-backend-domain", "meetings-backend", "meetings-cognito"]
    return out, [re.search(rf"delete-stack --stack-name {n}\s", out).start() for n in names]


def test_destroy_order_and_oidc_opt_in(sandbox):
    """TC-12.1 / TC-12.9."""
    out, positions = _destroy_order(sandbox)
    assert positions == sorted(positions)
    assert "meetings-github-oidc" not in out

    out, _ = _destroy_order(sandbox, DESTROY_OIDC="1")
    assert out.index("delete-stack --stack-name meetings-github-oidc") > out.index(
        "delete-stack --stack-name meetings-cognito"
    )

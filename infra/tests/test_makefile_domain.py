"""`DOMAIN` derivation, empty-domain fail-fast and no-silent-detach in the `Makefile`
(FR-8, AC-13 frontend half).

Run under both `MAKE_BIN=/usr/bin/make` (3.81) and GNU make 4.x.
"""

import pytest

FRONTEND_DOMAIN_TARGETS = [
    "aws-frontend-cert",
    "aws-frontend-https",
    "aws-frontend-cert-status",
    "aws-frontend-dns",
    "aws-frontend-https-check",
]
DOMAIN_PARAMS = ("CertificateArn", "DomainName", "HostedZoneId")
CERT_ARN = "arn:aws:acm:us-east-1:123456789012:certificate/abc"


def _print_vars(sandbox, *names, vars=None, env=None):
    """Like `sandbox.print_vars`, but with command-line variables and extra env."""
    wrapper = sandbox.root / "Makefile.print_vars"
    lines = ["include Makefile", "", ".PHONY: _print", "_print:"]
    lines += [f'\t@echo "{name}=[$({name})]"' for name in names]
    wrapper.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = sandbox.run("-f", wrapper.name, "_print", vars=vars, env=env)
    assert result.returncode == 0, result.stderr
    values = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and value.startswith("[") and value.endswith("]"):
            values[key] = value[1:-1]
    return values


def _frontend_deploy_args(sandbox):
    calls = [
        c
        for c in sandbox.calls("aws")
        if c["argv"][:2] == ["cloudformation", "deploy"] and "infra/frontend.yaml" in c["argv"]
    ]
    assert len(calls) == 1, "expected exactly one frontend cloudformation deploy call"
    return calls[0]["argv"]


# --- TC-6.10: derivation ---------------------------------------------------------------------


def test_domain_unset_yields_empty_subdomains(sandbox):
    """TC-6.10: no `DOMAIN` means empty subdomains, never a dangling `app.`/`api.`."""
    values = _print_vars(sandbox, "DOMAIN", "FRONTEND_DOMAIN", "BACKEND_DOMAIN")

    assert values == {"DOMAIN": "", "FRONTEND_DOMAIN": "", "BACKEND_DOMAIN": ""}


@pytest.mark.parametrize("source", ["command-line", "environment", "env-file"])
def test_domain_drives_both_subdomains(sandbox, source):
    """TC-6.10: `DOMAIN=ex.com` gives `app.ex.com` / `api.ex.com`, from any source."""
    kwargs = {}
    if source == "command-line":
        kwargs["vars"] = {"DOMAIN": "ex.com"}
    elif source == "environment":
        kwargs["env"] = {"DOMAIN": "ex.com"}
    else:
        sandbox.write_env("DOMAIN=ex.com\n")

    values = _print_vars(sandbox, "FRONTEND_DOMAIN", "BACKEND_DOMAIN", **kwargs)

    assert values == {"FRONTEND_DOMAIN": "app.ex.com", "BACKEND_DOMAIN": "api.ex.com"}


def test_individual_subdomain_overrides_win(sandbox):
    """TC-6.10: `FRONTEND_DOMAIN`/`BACKEND_DOMAIN` stay individually overridable."""
    values = _print_vars(
        sandbox,
        "FRONTEND_DOMAIN",
        "BACKEND_DOMAIN",
        vars={"DOMAIN": "ex.com", "FRONTEND_DOMAIN": "www.other.org"},
    )
    assert values == {"FRONTEND_DOMAIN": "www.other.org", "BACKEND_DOMAIN": "api.ex.com"}

    env = {"BACKEND_DOMAIN": "api.other.org"}
    values = _print_vars(sandbox, "FRONTEND_DOMAIN", "BACKEND_DOMAIN", env=env)
    assert values == {"FRONTEND_DOMAIN": "", "BACKEND_DOMAIN": "api.other.org"}


def test_no_lecturer_domain_default(repo_root):
    """FR-8: the old `onetwothree.dobosevych.com` default is gone."""
    assert "onetwothree.dobosevych.com" not in (repo_root / "Makefile").read_text(encoding="utf-8")


# --- TC-6.9 (frontend half): fail fast on an empty domain ------------------------------------


@pytest.mark.parametrize("target", FRONTEND_DOMAIN_TARGETS)
def test_frontend_domain_targets_fail_fast_when_empty(sandbox, target):
    """TC-6.9: cert/HTTPS/DNS targets stop with an "empty" message before any external call."""
    result = sandbox.run(target)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "FRONTEND_DOMAIN is empty" in output
    assert "DOMAIN=" in output
    assert sandbox.calls() == [], "no aws/cert.sh/curl/dig call may happen on the empty-domain path"


def test_frontend_cert_runs_when_domain_set(sandbox):
    """The guard lets a configured domain through (cert.sh is asked for app.<DOMAIN>)."""
    sandbox.stub("aws", match=["acm", "list-certificates"], stdout=CERT_ARN)
    sandbox.stub("aws", match=["acm", "describe-certificate"], stdout="ISSUED")

    sandbox.run("aws-frontend-cert", vars={"DOMAIN": "ex.com"})

    assert any(c["argv"][:1] == ["sts"] for c in sandbox.calls("aws")), "aws-check should run"
    acm_calls = [c for c in sandbox.calls("aws") if c["argv"][:1] == ["acm"]]
    assert acm_calls, "cert.sh should query ACM for the domain"
    assert any("app.ex.com" in " ".join(c["argv"]) for c in acm_calls)


# --- TC-5.10 / TC-5.12: no silent detach -----------------------------------------------------


@pytest.mark.parametrize("vars", [None, {"DOMAIN": "ex.com"}], ids=["domain-unset", "cert-not-issued"])
def test_frontend_stack_omits_domain_params_without_certificate(sandbox, vars):
    """TC-5.10: without a domain (or before its certificate is issued) the domain
    parameters are omitted, so CloudFormation keeps their previous values."""
    result = sandbox.run("aws-frontend-stack", vars=vars)

    assert result.returncode == 0, result.stdout + result.stderr
    args = _frontend_deploy_args(sandbox)
    for param in DOMAIN_PARAMS:
        assert not any(a.startswith(f"{param}=") for a in args), f"{param}= must be omitted"


def test_frontend_stack_attaches_issued_certificate(sandbox):
    """With `DOMAIN` set and an issued certificate, all three parameters carry values."""
    sandbox.stub("aws", match=["acm", "list-certificates"], stdout=CERT_ARN)
    sandbox.stub("aws", match=["route53", "list-hosted-zones-by-name"], stdout="/hostedzone/Z123EXAMPLE")

    result = sandbox.run("aws-frontend-stack", vars={"DOMAIN": "ex.com"})

    assert result.returncode == 0, result.stdout + result.stderr
    args = _frontend_deploy_args(sandbox)
    assert f"CertificateArn={CERT_ARN}" in args
    assert "DomainName=app.ex.com" in args
    assert "HostedZoneId=Z123EXAMPLE" in args


@pytest.mark.parametrize("vars", [{"DETACH_DOMAIN": "1"}, {"DETACH_DOMAIN": "1", "DOMAIN": "ex.com"}])
def test_detach_domain_clears_domain_params(sandbox, vars):
    """TC-5.12: `DETACH_DOMAIN=1` explicitly passes all three parameters empty."""
    sandbox.stub("aws", match=["acm", "list-certificates"], stdout=CERT_ARN)

    result = sandbox.run("aws-frontend-stack", vars=vars)

    assert result.returncode == 0, result.stdout + result.stderr
    args = _frontend_deploy_args(sandbox)
    for param in DOMAIN_PARAMS:
        assert f"{param}=" in args, f"{param}= (empty) expected with DETACH_DOMAIN=1"
        assert not any(a.startswith(f"{param}=") and a != f"{param}=" for a in args)


def test_detach_domain_is_documented_in_help(sandbox):
    """FR-8: the escape hatch is documented where users look for it."""
    result = sandbox.run("help")

    assert "DETACH_DOMAIN=1" in result.stdout

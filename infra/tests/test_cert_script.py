"""Characterization tests for `infra/scripts/cert.sh`, run directly (not
through `make`) against the stub bin/ built by `MakeSandbox`."""


def _run_cert_sh(sandbox, *args):
    return sandbox.run_script("infra/scripts/cert.sh", *args, env={"PROJECT_NAME": "meetings"})


def _upsert_calls(sandbox):
    return [
        c
        for c in sandbox.calls("aws")
        if "route53" in c["argv"] and "change-resource-record-sets" in c["argv"]
    ]


def _request_cert_calls(sandbox):
    return [c for c in sandbox.calls("aws") if "acm" in c["argv"] and "request-certificate" in c["argv"]]


def test_reuse_issued_certificate_skips_request(sandbox):
    """TC-6.2: reusing an already-ISSUED certificate makes no
    request-certificate call and prints "Status: ISSUED"."""
    sandbox.stub(
        "aws",
        match=["acm", "list-certificates"],
        stdout="arn:aws:acm:us-east-1:123456789012:certificate/existing",
    )
    sandbox.stub(
        "aws",
        match=["acm", "describe-certificate", "Certificate.Status"],
        stdout="ISSUED",
    )

    result = _run_cert_sh(sandbox, "request", "app.example.com")

    assert result.returncode == 0, result.stderr
    assert "Status: ISSUED" in result.stdout
    assert not _request_cert_calls(sandbox)


def test_no_hosted_zone_prints_manual_instructions(sandbox):
    """TC-6.4: no Route 53 zone produces manual CNAME instructions and no UPSERT."""
    sandbox.stub(
        "aws",
        match=["acm", "request-certificate"],
        stdout="arn:aws:acm:us-east-1:123456789012:certificate/new1",
    )
    sandbox.stub(
        "aws",
        match=["acm", "describe-certificate", "DomainValidationOptions"],
        stdout="_acme-challenge.app.example.com\tvalidation-value",
    )
    # list-hosted-zones-by-name is left unstubbed: default fallback is empty
    # output, i.e. "no zone found" at every label level.

    result = _run_cert_sh(sandbox, "request", "app.example.com")

    assert result.returncode == 0, result.stderr
    assert not _upsert_calls(sandbox)
    assert "Add this record at the DNS provider" in result.stdout
    assert "_acme-challenge.app.example.com" in result.stdout


def test_validation_record_publish_times_out(sandbox):
    """TC-6.5: `sleep` stubbed; 20 empty polls for the validation record
    produce the timeout message on stderr and a non-zero exit."""
    sandbox.stub(
        "aws",
        match=["acm", "request-certificate"],
        stdout="arn:aws:acm:us-east-1:123456789012:certificate/new2",
    )
    # describe-certificate is left unstubbed for both the status query and the
    # validation-record query: the default empty output means "not ISSUED yet"
    # and "record not published yet" on every one of the 20 polls.

    result = _run_cert_sh(sandbox, "request", "app.example.com")

    assert result.returncode != 0
    assert "Timed out waiting for ACM to publish the validation record" in result.stderr


def test_expired_certificate_is_not_reused(sandbox):
    """TC-6.6: an EXPIRED prior cert is not matched by the ISSUED/PENDING_VALIDATION
    filter, so a brand-new request-certificate call is made."""
    # list-certificates --certificate-statuses ISSUED PENDING_VALIDATION is left
    # unstubbed: its default empty output stands in for "the only certificate on
    # file is EXPIRED, so this filtered query finds nothing".
    sandbox.stub(
        "aws",
        match=["acm", "request-certificate"],
        stdout="arn:aws:acm:us-east-1:123456789012:certificate/fresh",
    )
    sandbox.stub(
        "aws",
        match=["acm", "describe-certificate", "DomainValidationOptions"],
        stdout="_acme-challenge.app.example.com\tvalidation-value",
    )

    result = _run_cert_sh(sandbox, "request", "app.example.com")

    assert result.returncode == 0, result.stderr
    assert _request_cert_calls(sandbox), "expected a fresh request-certificate call for an EXPIRED cert"
    assert "Requested certificate for app.example.com" in result.stdout


def test_two_sibling_labels_share_one_zone_without_conflict(sandbox):
    """TC-6.7: `api.` and `app.` sibling labels both UPSERT into the same
    hosted zone, with two distinct record names."""
    sandbox.stub(
        "aws",
        match=["route53", "list-hosted-zones-by-name"],
        stdout="/hostedzone/Z1EXAMPLE",
    )
    sandbox.stub(
        "aws",
        match=["request-certificate", "api.example.com"],
        stdout="arn:aws:acm:us-east-1:123456789012:certificate/api-cert",
    )
    sandbox.stub(
        "aws",
        match=["request-certificate", "app.example.com"],
        stdout="arn:aws:acm:us-east-1:123456789012:certificate/app-cert",
    )
    sandbox.stub(
        "aws",
        match=["describe-certificate", "api-cert", "DomainValidationOptions"],
        stdout="_acme-api.example.com\tvalue-api",
    )
    sandbox.stub(
        "aws",
        match=["describe-certificate", "app-cert", "DomainValidationOptions"],
        stdout="_acme-app.example.com\tvalue-app",
    )

    result_api = _run_cert_sh(sandbox, "request", "api.example.com")
    result_app = _run_cert_sh(sandbox, "request", "app.example.com")

    assert result_api.returncode == 0, result_api.stderr
    assert result_app.returncode == 0, result_app.stderr

    upserts = _upsert_calls(sandbox)
    assert len(upserts) == 2

    def _zone_id(call):
        idx = call["argv"].index("--hosted-zone-id")
        return call["argv"][idx + 1]

    zones = {_zone_id(c) for c in upserts}
    assert zones == {"Z1EXAMPLE"}
    joined_calls = [" ".join(c["argv"]) for c in upserts]
    assert any("_acme-api.example.com" in c for c in joined_calls)
    assert any("_acme-app.example.com" in c for c in joined_calls)


def test_rerun_after_issued_is_a_no_op(sandbox):
    """TC-6.8: re-running `request` after the certificate is already ISSUED
    is a no-op: immediate "Status: ISSUED", no further DNS/API action."""
    sandbox.stub(
        "aws",
        match=["acm", "list-certificates"],
        stdout="arn:aws:acm:us-east-1:123456789012:certificate/existing",
    )
    sandbox.stub(
        "aws",
        match=["acm", "describe-certificate", "Certificate.Status"],
        stdout="ISSUED",
    )

    first = _run_cert_sh(sandbox, "request", "app.example.com")
    second = _run_cert_sh(sandbox, "request", "app.example.com")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "Status: ISSUED" in first.stdout
    assert "Status: ISSUED" in second.stdout
    assert not _request_cert_calls(sandbox)
    assert not _upsert_calls(sandbox)

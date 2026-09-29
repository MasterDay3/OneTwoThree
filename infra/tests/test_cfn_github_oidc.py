"""Static checks for `infra/github-oidc.yaml` (GitHub OIDC provider + deploy role).

Covers TC-4.1, TC-4.2, TC-4.6, TC-10.1a, TC-10.5 and TC-10.8 (FR-16, FR-17,
FR-18, NFR-1, NFR-2, AC-7 static half), plus the `ProviderManagedByStack`
output that TC-4.8's Makefile logic reads.
"""

from collections import defaultdict

import pytest

TEMPLATE = "github-oidc.yaml"

OIDC_HOST = "token.actions.githubusercontent.com"
SUB_KEY = f"{OIDC_HOST}:sub"
AUD_KEY = f"{OIDC_HOST}:aud"
TRUSTED_SUB = "repo:MasterDay3/OneTwoThree:ref:refs/heads/main"
AUDIENCE = "sts.amazonaws.com"

# FR-18: the complete, exact per-service action sets of the deploy role.
EXPECTED_ACTIONS = {
    "ecr": {
        "GetAuthorizationToken",
        "BatchCheckLayerAvailability",
        "InitiateLayerUpload",
        "UploadLayerPart",
        "CompleteLayerUpload",
        "PutImage",
        "BatchGetImage",
        "GetDownloadUrlForLayer",
        "DescribeImages",
    },
    "lambda": {"UpdateFunctionCode", "GetFunction", "GetFunctionConfiguration", "InvokeFunction"},
    "s3": {"ListBucket", "PutObject", "DeleteObject"},
    "cloudfront": {"CreateInvalidation"},
    "cloudformation": {"DescribeStacks"},
}

ECR_REPO_ARN = "arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}:repository/${ProjectName}-backend"
LAMBDA_ARN = "arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:${ProjectName}-backend"
BUCKET_ARN = "arn:${AWS::Partition}:s3:::${FrontendBucketName}"
DISTRIBUTION_ARN = (
    "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${FrontendDistributionId}"
)
STACK_ARN_PREFIX = "arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/"
FORBIDDEN_PREFIXES = ("iam:", "cognito-idp:", "cognito-identity:", "acm:", "route53:", "sts:")
STACK_SUFFIXES = ["backend-ecr", "backend", "backend-domain", "frontend", "cognito"]


# --------------------------------------------------------------------------- helpers


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _walk(node):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _resources_of_type(doc, cfn_type):
    return {name: res for name, res in doc["Resources"].items() if res["Type"] == cfn_type}


def _only(mapping, what):
    assert len(mapping) == 1, f"expected exactly one {what}, found {sorted(mapping)}"
    return next(iter(mapping.items()))


def _sub_string(value):
    """Return the template string of a `Fn::Sub` (plain strings pass through)."""
    if isinstance(value, dict):
        assert set(value) == {"Fn::Sub"}, f"expected Fn::Sub, got {value!r}"
        inner = value["Fn::Sub"]
        assert isinstance(inner, str), f"expected the short Fn::Sub form, got {inner!r}"
        return inner
    return value


@pytest.fixture
def doc(cfn_template):
    return cfn_template(TEMPLATE)


@pytest.fixture
def provider(doc):
    return _only(_resources_of_type(doc, "AWS::IAM::OIDCProvider"), "AWS::IAM::OIDCProvider")


@pytest.fixture
def role(doc):
    return _only(_resources_of_type(doc, "AWS::IAM::Role"), "AWS::IAM::Role")


@pytest.fixture
def trust_statement(role):
    statements = role[1]["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert len(statements) == 1, "the trust policy must have exactly one statement"
    return statements[0]


@pytest.fixture
def statements(role):
    policies = role[1]["Properties"].get("Policies", [])
    assert len(policies) == 1, "the deploy role must carry exactly one inline policy"
    assert "ManagedPolicyArns" not in role[1]["Properties"], "no managed policies on the deploy role"
    stmts = policies[0]["PolicyDocument"]["Statement"]
    assert stmts, "the inline policy has no statements"
    return stmts


@pytest.fixture
def by_sid(statements):
    sids = [s.get("Sid") for s in statements]
    assert len(set(sids)) == len(sids), f"duplicate Sids: {sids}"
    return {s["Sid"]: s for s in statements}


# --------------------------------------------------------------------------- parameters


def test_parameters_have_fixed_names_and_defaults(doc):
    params = doc["Parameters"]
    assert set(params) == {
        "ProjectName",
        "ExistingOidcProviderArn",
        "FrontendBucketName",
        "FrontendDistributionId",
    }
    assert params["ProjectName"]["Default"] == "meetings"
    assert params["ExistingOidcProviderArn"]["Default"] == ""
    assert "Default" not in params["FrontendBucketName"]
    assert "Default" not in params["FrontendDistributionId"]


# --------------------------------------------------------------------------- provider (TC-4.1, TC-4.2)


def test_provider_is_retained_and_trusts_github(provider):
    _, res = provider
    assert res["DeletionPolicy"] == "Retain"
    assert res["UpdateReplacePolicy"] == "Retain"
    props = res["Properties"]
    assert props["Url"] == f"https://{OIDC_HOST}"
    assert props["ClientIdList"] == [AUDIENCE]


def test_provider_creation_is_conditional_on_empty_existing_arn(doc, provider):
    _, res = provider
    assert res["Condition"] == "CreateOidcProvider"
    assert doc["Conditions"]["CreateOidcProvider"] == {"Fn::Equals": [{"Ref": "ExistingOidcProviderArn"}, ""]}


# ------------------------------------------------ role + trust (TC-4.1/4.2/4.6/10.1a/10.5)


def test_role_name_is_fixed_project_prefix(role):
    _, res = role
    assert res["Properties"]["RoleName"] == {"Fn::Sub": "${ProjectName}-github-deploy"}


def test_trust_federated_principal_picks_created_or_existing_provider(provider, trust_statement):
    provider_name, _ = provider
    assert trust_statement["Effect"] == "Allow"
    assert trust_statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert trust_statement["Principal"] == {
        "Federated": {
            "Fn::If": ["CreateOidcProvider", {"Ref": provider_name}, {"Ref": "ExistingOidcProviderArn"}],
        }
    }


def test_trust_condition_is_a_single_string_equals_on_sub_and_aud(trust_statement):
    condition = trust_statement["Condition"]
    assert set(condition) == {"StringEquals"}, f"only StringEquals is allowed, got {sorted(condition)}"
    assert condition["StringEquals"] == {SUB_KEY: TRUSTED_SUB, AUD_KEY: AUDIENCE}


def test_sub_is_an_exact_literal_for_this_fork(trust_statement):
    sub = trust_statement["Condition"]["StringEquals"][SUB_KEY]
    assert isinstance(sub, str), "the sub claim must be a literal string, not a parameter/intrinsic"
    assert sub == TRUSTED_SUB
    assert "MasterDay3/OneTwoThree" in sub
    assert "*" not in sub and "?" not in sub


def test_aud_is_an_independent_condition_key(trust_statement):
    string_equals = trust_statement["Condition"]["StringEquals"]
    assert AUD_KEY in string_equals and SUB_KEY in string_equals
    assert string_equals[AUD_KEY] == AUDIENCE


def test_no_string_like_anywhere_in_the_file(repo_root, doc):
    assert "StringLike" not in (repo_root / "infra" / TEMPLATE).read_text(encoding="utf-8")
    for node in _walk(doc):
        if isinstance(node, dict):
            assert not any("StringLike" in str(k) for k in node)


# --------------------------------------------------------------------------- permissions (TC-10.8)


def test_actions_per_service_match_fr18_exactly(statements):
    actual = defaultdict(set)
    for stmt in statements:
        assert stmt["Effect"] == "Allow"
        assert "NotAction" not in stmt and "NotResource" not in stmt
        for action in _as_list(stmt["Action"]):
            assert isinstance(action, str)
            assert "*" not in action and "?" not in action, f"wildcard action {action!r}"
            service, _, name = action.partition(":")
            actual[service].add(name)
    assert dict(actual) == EXPECTED_ACTIONS


def test_only_get_authorization_token_uses_star_resource(repo_root, statements):
    star = [s for s in statements if "*" in _as_list(s["Resource"])]
    assert len(star) == 1, "exactly one statement may use Resource '*'"
    assert _as_list(star[0]["Resource"]) == ["*"]
    assert _as_list(star[0]["Action"]) == ["ecr:GetAuthorizationToken"]
    text = (repo_root / "infra" / TEMPLATE).read_text(encoding="utf-8")
    assert text.count('"*"') == 1, "the file may contain only one bare '*' resource"


def test_forbidden_permissions_are_absent(statements):
    actions = [a for s in statements for a in _as_list(s["Action"])]
    for action in actions:
        assert not action.startswith(FORBIDDEN_PREFIXES), f"forbidden action {action!r}"
        if action.startswith("cloudformation:"):
            assert action == "cloudformation:DescribeStacks"
    assert "lambda:InvokeFunctionUrl" not in actions


def test_ecr_statements_are_scoped(by_sid):
    auth = by_sid["EcrAuth"]
    assert _as_list(auth["Action"]) == ["ecr:GetAuthorizationToken"]
    push = by_sid["EcrPush"]
    assert {a.split(":", 1)[1] for a in push["Action"]} == EXPECTED_ACTIONS["ecr"] - {"GetAuthorizationToken"}
    assert [_sub_string(r) for r in _as_list(push["Resource"])] == [ECR_REPO_ARN]


def test_lambda_statement_is_scoped_to_the_backend_function(by_sid):
    stmt = by_sid["LambdaDeploy"]
    assert {a.split(":", 1)[1] for a in stmt["Action"]} == EXPECTED_ACTIONS["lambda"]
    assert [_sub_string(r) for r in _as_list(stmt["Resource"])] == [LAMBDA_ARN]


def test_s3_statements_are_scoped_to_the_frontend_bucket(by_sid):
    listing = by_sid["S3List"]
    assert _as_list(listing["Action"]) == ["s3:ListBucket"]
    assert [_sub_string(r) for r in _as_list(listing["Resource"])] == [BUCKET_ARN]
    objects = by_sid["S3Objects"]
    assert set(objects["Action"]) == {"s3:PutObject", "s3:DeleteObject"}
    assert [_sub_string(r) for r in _as_list(objects["Resource"])] == [f"{BUCKET_ARN}/*"]


def test_cloudfront_statement_is_scoped_to_the_distribution(by_sid):
    stmt = by_sid["CloudFrontInvalidate"]
    assert _as_list(stmt["Action"]) == ["cloudfront:CreateInvalidation"]
    assert [_sub_string(r) for r in _as_list(stmt["Resource"])] == [DISTRIBUTION_ARN]


def test_cloudformation_statement_is_read_only_on_project_stacks(by_sid):
    stmt = by_sid["CfnRead"]
    assert _as_list(stmt["Action"]) == ["cloudformation:DescribeStacks"]
    resources = sorted(_sub_string(r) for r in _as_list(stmt["Resource"]))
    expected = sorted(f"{STACK_ARN_PREFIX}${{ProjectName}}-{suffix}/*" for suffix in STACK_SUFFIXES)
    assert resources == expected


def test_statement_sids_are_exactly_the_fr18_set(by_sid):
    assert set(by_sid) == {
        "EcrAuth",
        "EcrPush",
        "LambdaDeploy",
        "S3List",
        "S3Objects",
        "CloudFrontInvalidate",
        "CfnRead",
    }


# --------------------------------------------------------------------------- outputs / coupling


def test_outputs_expose_role_provider_and_management_flag(doc, provider, role):
    provider_name, _ = provider
    role_name, _ = role
    outputs = doc["Outputs"]
    assert set(outputs) >= {"DeployRoleArn", "OidcProviderArn", "ProviderManagedByStack"}
    assert outputs["DeployRoleArn"]["Value"] == {"Fn::GetAtt": [role_name, "Arn"]}
    assert outputs["OidcProviderArn"]["Value"] == {
        "Fn::If": ["CreateOidcProvider", {"Ref": provider_name}, {"Ref": "ExistingOidcProviderArn"}]
    }
    assert outputs["ProviderManagedByStack"]["Value"] == {"Fn::If": ["CreateOidcProvider", "true", "false"]}


def test_no_cross_stack_exports_or_imports(doc):
    for output in doc.get("Outputs", {}).values():
        assert "Export" not in output
    for node in _walk(doc):
        if isinstance(node, dict):
            assert "Fn::ImportValue" not in node


def test_resources_are_tagged_with_project_name(provider, role):
    for _, res in (provider, role):
        tags = res["Properties"]["Tags"]
        assert {"Key": "PROJECT_NAME", "Value": {"Ref": "ProjectName"}} in tags

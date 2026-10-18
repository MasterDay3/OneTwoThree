"""Static checks for `infra/ai.yml` (Paste an agenda: the agenda-parser Lambda and the budget alarm)."""

import pytest

TEMPLATE = "ai.yml"


@pytest.fixture
def doc(cfn_template):
    return cfn_template(TEMPLATE)


def test_parser_reuses_the_backend_image_outside_any_vpc(doc):
    fn = doc["Resources"]["ParserFunction"]["Properties"]
    assert fn["PackageType"] == "Image"
    assert fn["Code"] == {"ImageUri": {"Ref": "ImageUri"}}
    assert fn["ImageConfig"] == {"Command": ["app.ai.agenda_lambda.handler"]}
    assert "VpcConfig" not in fn
    assert fn["Timeout"] == 30 and fn["MemorySize"] == 512
    assert fn["FunctionName"] == {"Fn::Sub": "${ProjectName}-agenda-parser"}


def test_model_and_cognito_come_from_parameters(doc):
    env = doc["Resources"]["ParserFunction"]["Properties"]["Environment"]["Variables"]
    assert env["PARSER_MODEL"] == {"Ref": "ParserModel"}
    assert doc["Parameters"]["ParserModel"]["Default"] == "anthropic.claude-opus-5-5"
    assert env["COGNITO_USER_POOL_ID"] == {"Ref": "CognitoUserPoolId"}
    assert env["COGNITO_CLIENT_ID"] == {"Ref": "CognitoClientId"}
    # An empty pool ID would turn the check off, so the stack refuses one.
    assert doc["Parameters"]["CognitoUserPoolId"]["AllowedPattern"] == ".+"
    assert "COGNITO_JWKS" not in env  # outside the VPC it fetches the keys itself


def test_role_may_only_log_and_call_bedrock(doc):
    role = doc["Resources"]["ParserRole"]["Properties"]
    assert role["ManagedPolicyArns"] == [
        {"Fn::Sub": "arn:${AWS::Partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"}
    ]
    (policy,) = role["Policies"]
    assert policy["PolicyDocument"]["Statement"] == [
        {"Effect": "Allow", "Action": "bedrock-mantle:CreateInference", "Resource": "*"}
    ]


def test_public_url_takes_post_from_the_site_only(doc):
    url = doc["Resources"]["ParserFunctionUrl"]["Properties"]
    assert url["AuthType"] == "NONE"
    assert url["Cors"]["AllowOrigins"] == {"Ref": "AllowedOrigins"}
    assert url["Cors"]["AllowMethods"] == ["POST"]
    assert set(url["Cors"]["AllowHeaders"]) == {"authorization", "content-type"}
    permissions = {
        p["Properties"]["Action"]: p["Properties"]
        for p in doc["Resources"].values()
        if p["Type"] == "AWS::Lambda::Permission"
    }
    assert permissions["lambda:InvokeFunctionUrl"]["FunctionUrlAuthType"] == "NONE"
    assert permissions["lambda:InvokeFunction"]["InvokedViaFunctionUrl"] is True


def test_budget_alarm_emails_at_five_dollars(doc):
    budget = doc["Resources"]["MonthlyBudget"]["Properties"]
    assert doc["Parameters"]["BudgetLimitUsd"]["Default"] == 5
    assert budget["Budget"]["BudgetLimit"] == {"Amount": {"Ref": "BudgetLimitUsd"}, "Unit": "USD"}
    assert budget["Budget"]["TimeUnit"] == "MONTHLY" and budget["Budget"]["BudgetType"] == "COST"
    kinds = set()
    for entry in budget["NotificationsWithSubscribers"]:
        kinds.add(entry["Notification"]["NotificationType"])
        assert entry["Subscribers"] == [{"SubscriptionType": "EMAIL", "Address": {"Ref": "BudgetEmail"}}]
    assert kinds == {"ACTUAL", "FORECASTED"}


def test_every_taggable_resource_carries_the_project_tag(doc):
    tag = {"Key": "PROJECT_NAME", "Value": {"Ref": "ProjectName"}}
    for name in ("ParserLogGroup", "ParserRole", "ParserFunction"):
        assert tag in doc["Resources"][name]["Properties"]["Tags"], name
    assert tag in doc["Resources"]["MonthlyBudget"]["Properties"]["ResourceTags"]


def test_outputs_give_the_frontend_its_url(doc):
    assert doc["Outputs"]["ParserUrl"]["Value"] == {"Fn::GetAtt": ["ParserFunctionUrl", "FunctionUrl"]}

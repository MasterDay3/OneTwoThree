"""Static checks for `infra/backend-domain.yaml`, the API Gateway HTTP API that
serves the backend Lambda on `api.<domain>` (TC-7.1a-h, TC-7.8, TC-14.1a,
TC-14.3)."""

import pytest

TEMPLATE = "backend-domain.yaml"

FIXED_PARAMETERS = (
    "ProjectName",
    "BackendFunctionName",
    "DomainName",
    "CertificateArn",
    "HostedZoneId",
    "ThrottleBurstLimit",
    "ThrottleRateLimit",
)


@pytest.fixture
def doc(cfn_template):
    return cfn_template(TEMPLATE)


def _resources_of_type(doc, resource_type):
    return {
        name: resource for name, resource in doc["Resources"].items() if resource["Type"] == resource_type
    }


def _single(doc, resource_type):
    found = _resources_of_type(doc, resource_type)
    assert len(found) == 1, f"expected exactly one {resource_type}, found {sorted(found)}"
    return next(iter(found.items()))


def _walk(node, visit):
    visit(node)
    if isinstance(node, dict):
        for value in node.values():
            _walk(value, visit)
    elif isinstance(node, list):
        for item in node:
            _walk(item, visit)


def _sub_string(value):
    """Return the template string of an `Fn::Sub` (short or list form)."""
    assert isinstance(value, dict), f"expected an Fn::Sub, got {value!r}"
    assert "Fn::Sub" in value, f"expected an Fn::Sub, got {value!r}"
    sub = value["Fn::Sub"]
    return sub[0] if isinstance(sub, list) else sub


# --- Parameters -----------------------------------------------------------


def test_fixed_parameter_names_exist(doc):
    params = doc["Parameters"]
    for name in FIXED_PARAMETERS:
        assert name in params, f"missing parameter {name}"


def test_parameter_defaults(doc):
    params = doc["Parameters"]
    assert params["ProjectName"]["Default"] == "meetings"
    assert params["HostedZoneId"]["Default"] == ""
    assert params["ThrottleBurstLimit"]["Default"] == 100
    assert params["ThrottleRateLimit"]["Default"] == 50


# --- TC-7.1a: API, integration --------------------------------------------


def test_tc_7_1a_http_api_disables_default_endpoint(doc):
    _, api = _single(doc, "AWS::ApiGatewayV2::Api")
    props = api["Properties"]
    assert props["ProtocolType"] == "HTTP"
    assert props["DisableExecuteApiEndpoint"] is True


def test_tc_7_1a_integration_is_lambda_proxy_v2(doc):
    _, integration = _single(doc, "AWS::ApiGatewayV2::Integration")
    props = integration["Properties"]
    assert props["IntegrationType"] == "AWS_PROXY"
    assert props["PayloadFormatVersion"] == "2.0"
    assert props["TimeoutInMillis"] == 30000
    assert props["ApiId"] == {"Ref": "HttpApi"}
    uri = _sub_string(props["IntegrationUri"])
    assert uri.startswith("arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:")
    assert uri.endswith("${BackendFunctionName}")


# --- TC-7.1b: route and stage ---------------------------------------------


def test_tc_7_1b_default_route_targets_integration(doc):
    _, route = _single(doc, "AWS::ApiGatewayV2::Route")
    props = route["Properties"]
    assert props["RouteKey"] == "$default"
    target = _sub_string(props["Target"])
    assert target == "integrations/${LambdaIntegration}"


def test_tc_7_1b_default_stage_autodeploys_with_throttling(doc):
    _, stage = _single(doc, "AWS::ApiGatewayV2::Stage")
    props = stage["Properties"]
    assert props["StageName"] == "$default"
    assert props["AutoDeploy"] is True
    assert "ThrottleSettings" not in props, "HTTP API stages have no ThrottleSettings (REST-API-only shape)"
    settings = props["DefaultRouteSettings"]
    assert settings["ThrottlingBurstLimit"] == {"Ref": "ThrottleBurstLimit"}
    assert settings["ThrottlingRateLimit"] == {"Ref": "ThrottleRateLimit"}


# --- TC-7.1c: invoke permission scoping -----------------------------------


def test_tc_7_1c_invoke_permission_scoped_to_this_api(doc):
    _, permission = _single(doc, "AWS::Lambda::Permission")
    props = permission["Properties"]
    assert props["Action"] == "lambda:InvokeFunction"
    assert props["Principal"] == "apigateway.amazonaws.com"
    assert props["FunctionName"] == {"Ref": "BackendFunctionName"}
    source_arn = props["SourceArn"]
    assert source_arn != "*"
    arn = _sub_string(source_arn)
    assert arn == "arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${HttpApi}/*"
    assert "${HttpApi}" in arn


# --- TC-7.1d: custom domain and mapping -----------------------------------


def test_tc_7_1d_regional_domain_with_tls_1_2(doc):
    _, domain = _single(doc, "AWS::ApiGatewayV2::DomainName")
    props = domain["Properties"]
    assert props["DomainName"] == {"Ref": "DomainName"}
    config = props["DomainNameConfigurations"][0]
    assert config["CertificateArn"] == {"Ref": "CertificateArn"}
    assert config["EndpointType"] == "REGIONAL"
    assert config["SecurityPolicy"] == "TLS_1_2"


def test_tc_7_1d_root_api_mapping_on_default_stage(doc):
    _, mapping = _single(doc, "AWS::ApiGatewayV2::ApiMapping")
    props = mapping["Properties"]
    assert props.get("ApiMappingKey", "") == ""
    assert props["ApiId"] == {"Ref": "HttpApi"}
    assert props["DomainName"] == {"Ref": "ApiDomain"}
    # The Ref orders the mapping after the stage; cfn-lint (W3005) rejects a redundant DependsOn.
    assert props["Stage"] == {"Ref": "DefaultStage"}


# --- TC-7.1e: conditional Route 53 alias -----------------------------------


def test_tc_7_1e_dns_condition_keyed_on_hosted_zone(doc):
    condition = doc["Conditions"]["CreateDnsRecord"]
    assert condition == {"Fn::Not": [{"Fn::Equals": [{"Ref": "HostedZoneId"}, ""]}]}


def test_tc_7_1e_alias_a_record_to_regional_domain(doc):
    records = _resources_of_type(doc, "AWS::Route53::RecordSet")
    a_records = [r for r in records.values() if r["Properties"]["Type"] == "A"]
    assert len(a_records) == 1
    record = a_records[0]
    assert record["Condition"] == "CreateDnsRecord"
    props = record["Properties"]
    assert props["HostedZoneId"] == {"Ref": "HostedZoneId"}
    assert props["Name"] == {"Ref": "DomainName"}
    alias = props["AliasTarget"]
    assert alias["DNSName"] == {"Fn::GetAtt": ["ApiDomain", "RegionalDomainName"]}
    assert alias["HostedZoneId"] == {"Fn::GetAtt": ["ApiDomain", "RegionalHostedZoneId"]}
    for record in records.values():
        assert record.get("Condition") == "CreateDnsRecord"


# --- TC-7.1f: outputs ------------------------------------------------------


def test_tc_7_1f_api_url_has_trailing_slash(doc):
    api_url = _sub_string(doc["Outputs"]["ApiUrl"]["Value"])
    assert api_url == "https://${DomainName}/"
    assert api_url.endswith("/")


def test_api_docs_url_output(doc):
    docs_url = _sub_string(doc["Outputs"]["ApiDocsUrl"]["Value"])
    assert docs_url == "https://${DomainName}/api/docs"


# --- TC-7.1g: no gateway CORS ---------------------------------------------


def test_tc_7_1g_no_cors_configuration(doc):
    _, api = _single(doc, "AWS::ApiGatewayV2::Api")
    assert "CorsConfiguration" not in api["Properties"]


# --- TC-7.1h: no cross-stack coupling -------------------------------------


def test_tc_7_1h_no_exports_or_import_values(doc):
    for name, output in doc.get("Outputs", {}).items():
        assert "Export" not in output, f"output {name} has an Export"

    imports = []

    def visit(node):
        if isinstance(node, dict) and "Fn::ImportValue" in node:
            imports.append(node)

    _walk(doc, visit)
    assert not imports, f"found Fn::ImportValue: {imports}"
    assert doc["Parameters"]["BackendFunctionName"]["Type"] == "String"


# --- TC-7.8 / TC-14.1a / TC-14.3: timeout budgets -------------------------


def test_tc_7_8_and_14_1a_timeout_budgets_match(cfn_template, doc):
    backend = cfn_template("backend.yaml")
    assert backend["Parameters"]["Timeout"]["Default"] == 30
    _, integration = _single(doc, "AWS::ApiGatewayV2::Integration")
    assert integration["Properties"]["TimeoutInMillis"] == 30000
    assert integration["Properties"]["TimeoutInMillis"] == backend["Parameters"]["Timeout"]["Default"] * 1000


def test_tc_14_3_integration_timeout_is_a_literal(doc):
    _, integration = _single(doc, "AWS::ApiGatewayV2::Integration")
    timeout = integration["Properties"]["TimeoutInMillis"]
    assert isinstance(timeout, int) and not isinstance(timeout, bool)
    assert timeout == 30000


# --- Tagging ----------------------------------------------------------------


@pytest.mark.parametrize(
    "resource_type",
    ["AWS::ApiGatewayV2::Api", "AWS::ApiGatewayV2::Stage", "AWS::ApiGatewayV2::DomainName"],
)
def test_taggable_resources_carry_project_name_tag(doc, resource_type):
    _, resource = _single(doc, resource_type)
    # ApiGatewayV2 tags are a key/value map, not a list of {Key, Value}.
    assert resource["Properties"]["Tags"] == {"PROJECT_NAME": {"Ref": "ProjectName"}}

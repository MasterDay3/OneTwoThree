"""Characterization tests for `cfn_yaml.CfnLoader` against the 4 real
`infra/*.yaml` templates that exist today."""

import pytest

EXISTING_TEMPLATES = ["backend-ecr.yaml", "backend.yaml", "cognito.yaml", "frontend.yaml"]


@pytest.mark.parametrize("name", EXISTING_TEMPLATES)
def test_existing_template_loads(cfn_template, name):
    doc = cfn_template(name)
    assert doc["AWSTemplateFormatVersion"] == "2010-09-09"
    assert "Resources" in doc
    assert doc["Resources"], f"{name} has no resources"


def test_backend_timeout_default_is_30(cfn_template):
    doc = cfn_template("backend.yaml")
    assert doc["Parameters"]["Timeout"]["Default"] == 30


def _walk(node, visit):
    visit(node)
    if isinstance(node, dict):
        for value in node.values():
            _walk(value, visit)
    elif isinstance(node, list):
        for item in node:
            _walk(item, visit)


def test_intrinsics_come_out_in_long_form(cfn_template):
    doc = cfn_template("frontend.yaml")
    found = {"Ref": False, "Fn::Sub": False, "Fn::If": False, "Fn::Equals": False, "Fn::Not": False}

    def visit(node):
        if isinstance(node, dict):
            for key in found:
                if key in node:
                    found[key] = True

    _walk(doc, visit)

    for key, was_found in found.items():
        assert was_found, f"expected at least one long-form {key!r} in frontend.yaml"


def test_getatt_dotted_short_form_becomes_a_list(cfn_template):
    doc = cfn_template("backend.yaml")
    getatts = []

    def visit(node):
        if isinstance(node, dict) and "Fn::GetAtt" in node:
            getatts.append(node["Fn::GetAtt"])

    _walk(doc, visit)

    assert getatts, "expected at least one !GetAtt in backend.yaml"
    for value in getatts:
        assert isinstance(value, list), f"Fn::GetAtt value {value!r} was not converted to a list"
        assert len(value) == 2

    # `Value: !GetAtt BackendFunctionUrl.FunctionUrl` under Outputs.ApiUrl.
    assert doc["Outputs"]["ApiUrl"]["Value"] == {"Fn::GetAtt": ["BackendFunctionUrl", "FunctionUrl"]}

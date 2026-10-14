"""Static checks for `infra/reports.yaml` (weekly report), plus the inline report-mailer code run
against a fake boto3."""

import email
import sys
import types

import pytest

TEMPLATE = "reports.yaml"


@pytest.fixture
def doc(cfn_template):
    return cfn_template(TEMPLATE)


def _of_type(doc, resource_type):
    found = [r["Properties"] for r in doc["Resources"].values() if r["Type"] == resource_type]
    assert found, resource_type
    return found


def test_bucket_is_named_private_expires_and_notifies_the_mailer(doc):
    bucket = doc["Resources"]["ReportsBucket"]
    props = bucket["Properties"]
    assert props["BucketName"] == {"Fn::Sub": "${ProjectName}-reports-${AWS::AccountId}"}
    assert all(props["PublicAccessBlockConfiguration"].values())
    (rule,) = props["LifecycleConfiguration"]["Rules"]
    assert rule["ExpirationInDays"] == 90 and rule["Prefix"] == "reports/"
    (notification,) = props["NotificationConfiguration"]["LambdaConfigurations"]
    assert notification["Event"] == "s3:ObjectCreated:*"
    assert notification["Function"] == {"Fn::GetAtt": ["MailerFunction", "Arn"]}
    rules = {r["Name"]: r["Value"] for r in notification["Filter"]["S3Key"]["Rules"]}
    assert rules == {"prefix": "reports/", "suffix": ".csv"}
    # No cycle: the permission is built from the constructed name, and the bucket waits for it.
    assert bucket["DependsOn"] == "MailerInvokePermission"
    permission = doc["Resources"]["MailerInvokePermission"]["Properties"]
    assert permission["Principal"] == "s3.amazonaws.com"
    assert permission["SourceArn"] == {
        "Fn::Sub": "arn:${AWS::Partition}:s3:::${ProjectName}-reports-${AWS::AccountId}"
    }
    assert permission["SourceAccount"] == {"Ref": "AWS::AccountId"}


def test_queue_outlives_the_builder_and_has_a_dlq(doc):
    queue = doc["Resources"]["RequestQueue"]["Properties"]
    builder = doc["Resources"]["BuilderFunction"]["Properties"]
    assert builder["Timeout"] >= 60
    assert queue["VisibilityTimeout"] >= 6 * builder["Timeout"]
    assert queue["RedrivePolicy"] == {
        "deadLetterTargetArn": {"Fn::GetAtt": ["RequestDeadLetterQueue", "Arn"]},
        "maxReceiveCount": 3,
    }
    (mapping,) = _of_type(doc, "AWS::Lambda::EventSourceMapping")
    assert mapping["BatchSize"] == 1
    assert mapping["EventSourceArn"] == {"Fn::GetAtt": ["RequestQueue", "Arn"]}


def test_schedule_sends_a_message_every_monday_in_kyiv(doc):
    (schedule,) = _of_type(doc, "AWS::Scheduler::Schedule")
    assert doc["Parameters"]["ScheduleExpression"]["Default"] == "cron(0 7 ? * MON *)"
    assert doc["Parameters"]["ReportTimezone"]["Default"] == "Europe/Kyiv"
    assert schedule["ScheduleExpressionTimezone"] == {"Ref": "ReportTimezone"}
    assert schedule["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert schedule["Target"]["Arn"] == {"Fn::GetAtt": ["RequestQueue", "Arn"]}
    assert schedule["Target"]["Input"] == '{"week": null, "source": "schedule"}'
    statement = doc["Resources"]["SchedulerRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    assert statement == [
        {"Effect": "Allow", "Action": "sqs:SendMessage", "Resource": {"Fn::GetAtt": ["RequestQueue", "Arn"]}}
    ]


def test_builder_reuses_the_backend_image_with_another_handler(doc):
    builder = doc["Resources"]["BuilderFunction"]["Properties"]
    assert builder["PackageType"] == "Image"
    assert builder["ImageConfig"] == {"Command": ["app.reports.lambda_handler.handler"]}
    assert "VpcConfig" not in builder
    env = builder["Environment"]["Variables"]
    assert env["REPORTS_BUCKET"] == {"Fn::Sub": "${ProjectName}-reports-${AWS::AccountId}"}
    assert env["DB_IAM_AUTH"] == "true"


def test_mailer_can_read_and_send_but_never_write_reports(doc):
    mailer = doc["Resources"]["MailerFunction"]["Properties"]
    assert "VpcConfig" not in mailer
    assert mailer["Environment"]["Variables"]["SENDER"] == {"Fn::Sub": "reports@${Domain}"}
    actions = set()
    for policy in doc["Resources"]["MailerRole"]["Properties"]["Policies"]:
        for statement in policy["PolicyDocument"]["Statement"]:
            action = statement["Action"]
            actions.update([action] if isinstance(action, str) else action)
    assert actions == {"s3:GetObject", "ses:SendEmail", "ses:SendRawEmail"}


def test_every_taggable_resource_carries_project_name(doc):
    for name, resource in doc["Resources"].items():
        tags = resource.get("Properties", {}).get("Tags")
        if tags is not None:
            assert {"Key": "PROJECT_NAME", "Value": {"Ref": "ProjectName"}} in tags, name


def test_ses_identities_and_dkim_outputs(doc):
    identities = [p["EmailIdentity"] for p in _of_type(doc, "AWS::SES::EmailIdentity")]
    assert {"Ref": "Domain"} in identities
    assert len(identities) == 2
    for i in (1, 2, 3):
        assert doc["Outputs"][f"DkimName{i}"]["Value"] == {
            "Fn::GetAtt": ["DomainIdentity", f"DkimDNSTokenName{i}"]
        }


# --- the inline report-mailer -------------------------------------------------------------------


@pytest.fixture
def mailer(doc, monkeypatch):
    sent = []
    s3 = types.SimpleNamespace(
        get_object=lambda Bucket, Key: {"Body": types.SimpleNamespace(read=lambda: b"Metric,This week\n")}
    )

    def send_email(**kwargs):
        sent.append(kwargs)
        return {"MessageId": f"m-{len(sent)}"}

    clients = {"s3": s3, "sesv2": types.SimpleNamespace(send_email=send_email)}
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: clients[name]))
    monkeypatch.setenv("SENDER", "reports@example.com")
    monkeypatch.setenv("RECIPIENTS", "me@example.com")
    code = doc["Resources"]["MailerFunction"]["Properties"]["Code"]["ZipFile"]
    assert len(code) <= 4096, "inline Lambda code is limited to 4096 characters"
    module = types.ModuleType("index")
    exec(compile(code, "index.py", "exec"), module.__dict__)
    return module, sent


def test_mailer_emails_the_new_report_as_an_attachment(mailer, caplog):
    module, sent = mailer
    caplog.set_level("INFO", logger="report-mailer")
    event = {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "s3": {"bucket": {"name": "meetings-reports-1"}, "object": {"key": "reports/2026-W40.csv"}},
            }
        ]
    }

    assert module.handler(event, None) == {"sent": ["m-1"]}

    (call,) = sent
    message = email.message_from_bytes(call["Content"]["Raw"]["Data"])
    assert message["From"] == "reports@example.com"
    assert message["To"] == "me@example.com"
    assert message["Subject"] == "Weekly meetings report 2026-W40"
    (attachment,) = [part for part in message.walk() if part.get_filename()]
    assert attachment.get_filename() == "2026-W40.csv"
    assert attachment.get_content_type() == "text/csv"
    assert attachment.get_payload(decode=True) == b"Metric,This week\n"
    assert (
        "report-mailer triggered trigger=s3 event=ObjectCreated:Put key=reports/2026-W40.csv" in caplog.messages
    )

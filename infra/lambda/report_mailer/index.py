"""report-mailer: emails each reports/<week>.csv that appears in the bucket, as an attachment."""
import logging
import os
import urllib.parse
from email.message import EmailMessage

import boto3

log = logging.getLogger("report-mailer")
log.setLevel(logging.INFO)
s3 = boto3.client("s3")
ses = boto3.client("sesv2")


def handler(event, context):
    sent = []
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        log.info("report-mailer triggered trigger=s3 event=%s key=%s", record["eventName"], key)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        name = key.rsplit("/", 1)[-1]
        week = name.removesuffix(".csv")

        msg = EmailMessage()
        msg["Subject"] = f"Weekly meetings report {week}"
        msg["From"] = os.environ["SENDER"]
        msg["To"] = os.environ["RECIPIENTS"]
        msg.set_content(f"The meetings report for {week} is attached.\n")
        msg.add_attachment(body, maintype="text", subtype="csv", filename=name)

        response = ses.send_email(Content={"Raw": {"Data": msg.as_bytes()}})
        log.info("report emailed key=%s message_id=%s", key, response["MessageId"])
        sent.append(response["MessageId"])
    return {"sent": sent}

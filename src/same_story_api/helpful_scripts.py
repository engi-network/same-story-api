import json
import logging
import os
import time
from pathlib import Path

import boto3
import coloredlogs
import requests


def setup_logging(log_level=logging.INFO):
    logger = logging.getLogger()

    # Set log format to dislay the logger name to hunt down verbose logging modules
    fmt = "%(asctime)s %(name)-25s %(levelname)-8s %(message)s"

    coloredlogs.install(level=log_level, fmt=fmt, logger=logger)

    return logger


def allow_all_to_publish_to_sns(topic_arn):
    return """{{
        "Id": "Policy1654105353800",
        "Version": "2012-10-17",
        "Statement": [
            {{
            "Sid": "Stmt1654105351953",
            "Action": [
                "sns:Publish"
            ],
            "Effect": "Allow",
            "Resource": "{}",
            "Principal": "*"
            }}
        ]
        }}""".format(
        topic_arn
    )


def allow_sns_to_write_to_sqs(topic_arn, queue_arn):
    return """{{
        "Version":"2012-10-17",
        "Statement":[
            {{
            "Sid": "MyPolicy",
            "Effect": "Allow",
            "Principal": {{"AWS" : "*"}},
            "Action": "SQS:SendMessage",
            "Resource": "{}",
            "Condition":{{
                "ArnEquals":{{
                "aws:SourceArn": "{}"
                }}
            }}
            }}
        ]
        }}""".format(
        queue_arn, topic_arn
    )


log = setup_logging()

sns_client = boto3.client("sns")
s3_client = boto3.client("s3")

TOPIC_ARN = os.environ["TOPIC_ARN"]
BUCKET_NAME = os.environ.get("BUCKET_NAME", "same-story-api-dev")


class SNSFanoutSQS(object):
    """For testing only, in prod do this with Terraform"""

    def __init__(self, queue_name, topic_name, visibility_timeout=180, persist=False):
        self.sns = boto3.client("sns")
        self.sqs = boto3.client("sqs")
        self.queue_name = queue_name
        self.topic_name = topic_name
        self.visibility_timeout = visibility_timeout
        self.persist = persist

    def __enter__(self):
        log.info(f"creating {self.queue_name=}")
        r = self.sqs.create_queue(
            QueueName=self.queue_name,
            Attributes={"VisibilityTimeout": str(self.visibility_timeout)},
        )
        self.queue_url = r["QueueUrl"]
        r = self.sqs.get_queue_attributes(QueueUrl=self.queue_url, AttributeNames=["QueueArn"])
        self.queue_arn = r["Attributes"]["QueueArn"]
        log.info(f"{self.queue_url=} {self.queue_arn=}")
        log.info(f"creating {self.topic_name=}")
        r = self.sns.create_topic(Name=self.topic_name)
        self.topic_arn = r["TopicArn"]
        log.info(f"{self.topic_arn=}")
        r = self.sns.subscribe(
            TopicArn=self.topic_arn,
            Protocol="sqs",
            Endpoint=self.queue_arn,
            ReturnSubscriptionArn=True,
        )
        r = self.sns.set_topic_attributes(
            TopicArn=self.topic_arn,
            AttributeName="Policy",
            AttributeValue=allow_all_to_publish_to_sns(self.topic_arn),
        )
        r = self.sqs.set_queue_attributes(
            QueueUrl=self.queue_url,
            Attributes={"Policy": allow_sns_to_write_to_sqs(self.topic_arn, self.queue_arn)},
        )
        return self

    def publish(self, d):
        return self.sns.publish(
            TopicArn=self.topic_arn,
            Message=json.dumps(d),
        )

    def receive(self, wait_time=5):
        r = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_time,
        )
        for msg in r.get("Messages", []):
            yield json.loads(json.loads(msg["Body"])["Message"])
            receipt_handle = msg["ReceiptHandle"]
            # log.info(f"{receipt_handle=}")
            self.sqs.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle,
            )

    def __exit__(self, exc_type, exc_value, traceback):
        if not self.persist:
            log.info(f"deleting {self.queue_url=} {self.topic_arn=}")
            self.sqs.delete_queue(QueueUrl=self.queue_url)
            self.sns.delete_topic(TopicArn=self.topic_arn)


def check_url(url):
    assert requests.get(url).status_code == 200


def upload(key_name, body):
    return s3_client.put_object(Body=body, Bucket=BUCKET_NAME, Key=key_name)


def upload_file(local, remote):
    s3_client.upload_file(local, BUCKET_NAME, str(remote))


def download(key_name):
    r = s3_client.get_object(Bucket=BUCKET_NAME, Key=key_name)
    return r["Body"].read()


def delete(key_name):
    r = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=key_name)
    for obj in r["Contents"]:
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=obj["Key"])


def exists(key_name):
    r = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=key_name)
    return "Contents" in r


def upload_frame(prefix, spec_d):
    story = spec_d["story"]
    frame = f"{story}.png"
    button = Path(f"{prefix}/frames/{frame}")
    # upload the button image (this is the check frame from Figma)
    upload_file(f"test/data/{button.name}", button)


def get_results(spec_d, upload=True):
    check_id = spec_d["check_id"]
    prefix = f"checks/{check_id}"
    results = f"{prefix}/report/results.json"

    # create a temporary SNS -> SQS fanout to receive status updates
    with SNSFanoutSQS(
        f"{check_id}-same-story-test-queue", f"{check_id}-same-story-test-topic"
    ) as sns_sqs:
        # let the backend server know where we'd like to receive status updates
        spec_d["sns_topic_arn"] = sns_sqs.topic_arn

        if upload:
            upload_frame(prefix, spec_d)

        # publish the job
        sns_client.publish(
            TopicArn=TOPIC_ARN,
            Message=json.dumps(spec_d),
        )

        results_d = {"spec": spec_d, "status": []}
        done = False
        count = 100
        # receive status updates, break when done, error or timeout
        while not done and count:
            log.info(f"{count=}")
            for msg in sns_sqs.receive():
                log.info(f"received {msg=}")
                results_d["status"].append(msg)
                if msg["step"] == msg["step_count"] - 1 or "error" in msg:
                    done = True
            count -= 1
        if done:
            time.sleep(1)
            results_d["results"] = json.loads(download(results))

    log.info(f"got {results_d=}")
    return results_d

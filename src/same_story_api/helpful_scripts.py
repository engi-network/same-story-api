import json
import logging
import os
import shutil
import socket
import time
from contextlib import contextmanager
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


log = setup_logging()

sns_client = boto3.client("sns")
sqs_client = boto3.client("sqs")
s3_client = boto3.client("s3")
sts_client = boto3.client("sts")

for service in ["sns", "sqs", "s3", "sts"]:
    globals()[service] = boto3.client(service)


def setup_env(env=None, region=None):
    """env is one of dev, staging, production"""
    if region is None:
        region = boto3.session.Session().region_name
    if env is None:
        env = os.environ.get("ENV", "dev")
    name = f"same-story-api-{env}"
    account = sts_client.get_caller_identity()["Account"]
    sns_topic = f"arn:aws:sns:{region}:{account}:{name}"
    # a place to submit job requests
    os.environ["TOPIC_ARN"] = sns_topic
    log.info(f"{os.environ['TOPIC_ARN']=}")
    # the S3 bucket to store the input image and output screenshots and results
    os.environ["BUCKET_NAME"] = name
    log.info(f"{os.environ['BUCKET_NAME']=}")
    # a place for the backend server to dequeue job requests
    os.environ["QUEUE_URL"] = f"{sqs_client.meta._endpoint_url}/{account}/{name}"
    log.info(f"{os.environ['QUEUE_URL']=}")
    os.environ["DEFAULT_STATUS_TOPIC_ARN"] = f"{sns_topic}-status"
    log.info(f"{os.environ['DEFAULT_STATUS_TOPIC_ARN']=}")


@contextmanager
def set_directory(path):
    origin = Path().absolute()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(origin)


@contextmanager
def cleanup_directory(path):
    try:
        yield
    finally:
        if path.exists():
            shutil.rmtree(path)


def get_port():
    sock = socket.socket()
    sock.bind(("", 0))
    return sock.getsockname()[1]


def get_s3_url(suffix):
    return f"{s3_client.meta.endpoint_url}/{suffix}"


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


class SNSFanoutSQS(object):
    """For testing only, in prod do this with Terraform"""

    def __init__(self, queue_name, topic_name, visibility_timeout=180, persist=False):
        self.sns = sns_client
        self.sqs = sqs_client
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


class Client(object):
    def __init__(self):
        self.bucket_name = os.environ["BUCKET_NAME"]
        self.topic_arn = os.environ["TOPIC_ARN"]

    def upload(self, key_name, body):
        return s3_client.put_object(Body=body, Bucket=self.bucket_name, Key=key_name)

    def upload_file(self, local, remote):
        s3_client.upload_file(local, self.bucket_name, str(remote))

    def upload_frame(self, prefix, spec_d):
        story = spec_d["story"]
        frame = f"{story}.png"
        button = Path(f"{prefix}/frames/{frame}")
        # upload the button image (this is the check frame from Figma)
        self.upload_file(f"test/data/{button.name}", button)

    def download(self, key_name):
        r = s3_client.get_object(Bucket=self.bucket_name, Key=key_name)
        return r["Body"].read()

    def delete(self, key_name):
        r = s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=key_name)
        for obj in r["Contents"]:
            s3_client.delete_object(Bucket=self.bucket_name, Key=obj["Key"])

    def exists(self, key_name):
        r = s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=key_name)
        return "Contents" in r

    def get_results(self, spec_d, upload=True, callback=None):
        check_id = spec_d["check_id"]
        prefix = f"checks/{check_id}"
        results = f"{prefix}/report/results.json"
        if callback is None:
            callback = lambda _: None

        # create a temporary SNS -> SQS fanout to receive status updates
        with SNSFanoutSQS(
            f"{check_id}-same-story-test-queue", f"{check_id}-same-story-test-topic"
        ) as sns_sqs:
            # let the backend server know where we'd like to receive status updates
            spec_d["sns_topic_arn"] = sns_sqs.topic_arn

            if upload:
                self.upload_frame(prefix, spec_d)

            # publish the job
            sns_client.publish(
                TopicArn=self.topic_arn,
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
                    callback(msg)
                    results_d["status"].append(msg)
                    if msg["step"] == msg["step_count"] - 1 or "error" in msg:
                        done = True
                count -= 1
            if done:
                time.sleep(1)
                results_d["results"] = json.loads(self.download(results))

        log.info(f"got {results_d=}")
        return results_d

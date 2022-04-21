import json
import logging

import boto3
import coloredlogs


def setup_logging(log_level=logging.INFO):
    logger = logging.getLogger()

    # Set log format to dislay the logger name to hunt down verbose logging modules
    fmt = "%(asctime)s %(name)-25s %(levelname)-8s %(message)s"

    coloredlogs.install(level=log_level, fmt=fmt, logger=logger)

    return logger


def allow_sns_to_write_to_sqs(topic_arn, queue_arn):
    return """{{
        "Version":"2012-10-17",
        "Statement":[
            {{
            "Sid":"MyPolicy",
            "Effect":"Allow",
            "Principal" : {{"AWS" : "*"}},
            "Action":"SQS:SendMessage",
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


class SNSFanoutSQS(object):
    """For testing only, in prod do this with Terraform"""

    def __init__(self, queue_name, topic_name, visibility_timeout=180):
        self.sns = boto3.client("sns")
        self.sqs = boto3.client("sqs")
        self.queue_name = queue_name
        self.topic_name = topic_name
        self.visibility_timeout = visibility_timeout

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
        log.info(f"deleting {self.queue_url=} {self.topic_arn=}")
        self.sqs.delete_queue(QueueUrl=self.queue_url)
        self.sns.delete_topic(TopicArn=self.topic_arn)

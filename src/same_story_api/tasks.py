import os

from celery import Celery
from kombu.utils.url import safequote

from same_story_api.helpful_scripts import SNSFanoutSQS, get_name

aws_access_key = safequote(os.environ["AWS_ACCESS_KEY_ID"])
aws_secret_key = safequote(os.environ["AWS_SECRET_ACCESS_KEY"])
queue_name_prefix = safequote(get_name())

app = Celery(
    "tasks",
    broker=f"sqs://{aws_access_key}:{aws_secret_key}@",
    broker_transport_options={"queue_name_prefix": f"{queue_name_prefix}-"},
)


@app.task
def fanout_cleanup(topic_arn, queue_url):
    SNSFanoutSQS.load(topic_arn, queue_url).cleanup()

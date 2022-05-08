import json
import os
import time
from itertools import zip_longest
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from same_story_api.helpful_scripts import SNSFanoutSQS, setup_logging

sns_client = boto3.client("sns")
s3_client = boto3.client("s3")

TOPIC_ARN = os.environ["TOPIC_ARN"]
BUCKET_NAME = os.environ.get("BUCKET_NAME", "same-story")

_ = lambda s: s

STATUS_MESSAGES = [
    _("downloaded Figma check frame"),
    _("checked out code"),
    _("installed packages"),
    _("captured screenshots"),
    _("completed visual comparisons"),
    _("completed numeric comparisons"),
    _("uploaded screenshots"),
]

log = setup_logging()


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


@pytest.fixture
def success_spec():
    return {
        "check_id": str(uuid4()),
        "width": "800",
        "height": "600",
        "path": "Global/Components",
        "component": "Button",
        "story": "Button With Knobs",
        "repository": "engi-network/figma-plugin",
        "branch": "main",  # optional
        "commit": "b606897faec4ae0983930c2707845e5792a38255",  # optional
    }


class Request(object):
    def __init__(self, spec_d, upload=True):
        self.spec_d = spec_d
        self.upload = upload

    def __enter__(self):
        self.results = get_results(self.spec_d, upload=self.upload)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        cleanup(self.spec_d)


@pytest.fixture
def success_results(success_spec):
    with Request(success_spec) as req:
        yield req.results


@pytest.fixture
def error_results_repo(success_spec):
    success_spec["repository"] = "nonsense"
    with Request(success_spec) as req:
        yield req.results


@pytest.fixture
def error_results_commit(success_spec):
    success_spec["commit"] = "nonsense"
    with Request(success_spec) as req:
        yield req.results


@pytest.fixture
def error_results_branch(success_spec):
    success_spec["branch"] = "nonsense"
    with Request(success_spec) as req:
        yield req.results


@pytest.fixture
def error_results_frame(success_spec):
    with Request(success_spec, upload=False) as req:
        yield req.results


@pytest.fixture
def success_results_no_commit_branch(success_spec):
    del success_spec["branch"]
    del success_spec["commit"]
    with Request(success_spec) as req:
        yield req.results


@pytest.fixture
def success_results_private_repo(success_spec):
    del success_spec["branch"]
    del success_spec["commit"]
    success_spec["repository"] = "cck197/figma-plugin"
    success_spec["github_token"] = os.environ["GITHUB_TOKEN_2"]
    with Request(success_spec) as req:
        yield req.results


@pytest.fixture
def error_results_with_github_token(success_spec):
    success_spec["github_token"] = "nonsense"
    with Request(success_spec) as req:
        yield req.results


def check_spec_in_results(spec, results):
    # check spec got copied into results
    for key, val in spec.items():
        assert results[key] == val


def get_error(results, key):
    check_spec_in_results(results["spec"], results["results"])
    error = results["results"]["error"]
    for (i, msg), status in zip_longest(enumerate(STATUS_MESSAGES), results["status"]):
        error_ = status.get("error")
        if error_:
            assert error_ == error
            break
        else:
            assert status["step"] == i
            assert status["step_count"] == len(STATUS_MESSAGES)
            assert status["message"] == msg
    # key is the step where our job failed
    # stdout and stderr should tell us why
    assert set(error.keys()) >= set([key, "stdout", "stderr"])


def cleanup(spec):
    check_id = spec["check_id"]
    log.info(f"cleaning up {check_id=}")
    prefix = f"checks/{check_id}"
    # clean up the directory in S3
    delete(prefix)


def check_code_snippet_in_results(results):
    assert results["code_path"] == "src/app/components/global/Button/Button.stories.tsx"
    assert (
        results["code_snippet"] == "import { action } from '@storybook/addon-actions'\n"
        "import { boolean, select, text } from '@storybook/addon-knobs'\n\n"
        "import Button from './Button'\n\n"
    )


def test_should_be_able_to_successfully_run_check(success_results):
    results = success_results["results"]
    spec_d = success_results["spec"]
    assert not "error" in results

    for (i, msg), status in zip_longest(enumerate(STATUS_MESSAGES), success_results["status"]):
        assert status["step"] == i
        assert status["step_count"] == len(STATUS_MESSAGES)
        assert status["message"] == msg

    check_id = success_results["spec"]["check_id"]
    prefix = f"checks/{check_id}"
    gray_difference = f"{prefix}/report/gray_difference.png"
    blue_difference = gray_difference.replace("gray", "blue")
    button = f"{prefix}/report/__screenshots__/{spec_d['path']}/{spec_d['component']}/{spec_d['story']}.png"

    # check for the screenshot captured by storycap
    assert exists(button)
    # check for the output comparison images in S3
    assert exists(gray_difference)
    assert exists(blue_difference)

    # check for the objective visual difference between the check frame and the
    # screenshot captured by storycap
    mae = float(results["MAE"].split()[0])
    assert mae < 5.0
    # check timestamps
    assert results["completed_at"] > results["created_at"]
    check_spec_in_results(spec_d, results)
    check_code_snippet_in_results(results)


def test_should_be_able_to_successfully_run_check_no_branch_commit(
    success_results_no_commit_branch,
):
    return test_should_be_able_to_successfully_run_check(success_results_no_commit_branch)


def test_should_work_on_private_repo(success_results_private_repo):
    return test_should_be_able_to_successfully_run_check(success_results_private_repo)


def test_should_error_on_branch_problem(error_results_branch):
    get_error(error_results_branch, "branch")


def test_should_error_on_repo_problem(error_results_repo):
    get_error(error_results_repo, "clone")


def test_should_error_on_bad_github_token(error_results_with_github_token):
    get_error(error_results_with_github_token, "clone")


def test_should_error_on_commit_problem(error_results_commit):
    get_error(error_results_commit, "commit")


def test_should_error_on_missing_frame(error_results_frame):
    get_error(error_results_frame, "frame")

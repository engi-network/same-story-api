import os
from itertools import zip_longest
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from same_story_api.helpful_scripts import Client, setup_env, setup_logging

_ = lambda s: s

STATUS_MESSAGES = [
    _("job started"),
    _("downloaded Figma check frame"),
    _("checked out code"),
    _("installed packages"),
    _("captured screenshots"),
    _("completed visual comparisons"),
    _("completed numeric comparisons"),
    _("uploaded screenshots"),
]

log = setup_logging()

setup_env()

client = Client()


def check_url(url):
    assert requests.get(url).status_code == 200


@pytest.fixture
def success_spec():
    return {
        "check_id": str(uuid4()),
        "width": "800",
        "height": "600",
        "path": "Example",
        "component": "Button",
        "story": "Primary",
        "repository": "engi-network/same-story-storybook",
        "branch": "master",  # optional
        "commit": "7a9fe60aeb107ea26e6fb5aa466623170e25a8d7",  # optional
    }


class Request(object):
    def __init__(self, spec_d, upload=True):
        self.spec_d = spec_d
        self.upload = upload

    def __enter__(self):
        self.results = client.get_results(self.spec_d, Path("test/data"), upload=self.upload)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        check_id = self.spec_d["check_id"]
        log.info(f"cleaning up {check_id=}")
        prefix = f"checks/{check_id}"
        # clean up the directory in S3
        client.delete(prefix)


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
    success_spec["repository"] = success_spec["repository"].replace("engi-network", "cck197")
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


def check_code_snippet_in_results(results):
    assert results["code_path"] == "src/stories/Button.stories.jsx"
    assert (
        results["code_snippet"] == "import React from 'react';\n\n"
        "import { Button } from './Button';\n\n"
        "// More on default export: https://storybook.js.org/docs/react/writing-stories/introduction#default-export\n"
    )


# TODO check for code_snippet and url_screenshot on a given step
def check_status_messages(success_results):
    for (i, msg), status in zip_longest(enumerate(STATUS_MESSAGES), success_results["status"]):
        assert status["step"] == i
        assert status["step_count"] == len(STATUS_MESSAGES)
        assert status["message"] == msg


def test_should_be_able_to_successfully_run_check(success_results):
    results = success_results["results"]
    spec_d = success_results["spec"]
    assert not "error" in results
    check_status_messages(success_results)

    check_id = success_results["spec"]["check_id"]
    prefix = f"checks/{check_id}"
    gray_difference = f"{prefix}/report/gray_difference.png"
    blue_difference = gray_difference.replace("gray", "blue")
    button = f"{prefix}/report/__screenshots__/{spec_d['path']}/{spec_d['component']}/{spec_d['story']}.png"

    # check for the screenshot captured by storycap
    assert client.exists(button)
    # check for the output comparison images in S3
    assert client.exists(gray_difference)
    assert client.exists(blue_difference)

    # check for the objective visual difference between the check frame and the
    # screenshot captured by storycap
    mae = float(results["MAE"].split()[0])
    assert mae < 50.0
    # check timestamps
    duration = results["completed_at"] - results["created_at"]
    assert duration > 0 and duration < 200
    check_spec_in_results(spec_d, results)
    check_code_snippet_in_results(results)
    code_size = results["code_size"]
    assert code_size > 2000000 and code_size < 5000000
    for key in ("gray_difference", "blue_difference", "screenshot"):
        check_url(results[f"url_{key}"])


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

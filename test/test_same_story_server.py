import os
from copy import deepcopy
from itertools import zip_longest
from pathlib import Path
from uuid import uuid4

import pytest
import requests
from same_story_api.helpful_scripts import Client, log, setup_env

_ = lambda s: s

# (message, (key, ))
# where key is a key in results.json
STATUS_MESSAGES = [
    (_("job started"), ("created_at",)),
    (_("downloaded Figma check frame"), ("url_check_frame",)),
    (_("checked out code"), ("code_paths", "code_size", "code_snippets")),
    (_("installed packages"), ()),
    (_("captured screenshots"), ("url_screenshot",)),
    (_("completed visual comparisons"), ()),
    (_("completed numeric comparisons"), ("MAE",)),
    (_("uploaded screenshots"), ("url_blue_difference", "url_gray_difference", "completed_at")),
]

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
        "repository": "https://github.com/engi-network/same-story-storybook.git",
        "branch": "master",  # optional
        "commit": "61a8bd8",  # optional
        "args": {
            "0": {"name": "primary", "value": "false"},
            "1": {"name": "size", "value": "small"},
        },
    }


# a second request for a different repo that should succeed
# initially had trouble with this one b/c spaces in name
@pytest.fixture
def success_spec2():
    return {
        "args": {"0": {"name": "primary", "value": "false"}},
        "branch": "main",
        "check_id": str(uuid4()),
        "commit": "28d791f2d77de69ee038a15b9fd7783179a65b2d",
        "component": "Button",
        "height": "340",
        "name": "Button-Button With Knobs",
        "path": "Global/Components",
        "repository": "https://github.com/engi-network/figma-plugin.git",
        "story": "Button Story",
        "width": "439",
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
def success_results2(success_spec2):
    with Request(success_spec2) as req:
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
    success_spec["commit"] = None
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
def private_repo_spec(success_spec):
    spec = deepcopy(success_spec)
    del spec["branch"]
    del spec["commit"]
    spec["repository"] = spec["repository"].replace("engi-network", "cck197")
    yield spec


@pytest.fixture
def success_results_private_repo(private_repo_spec):
    spec = deepcopy(private_repo_spec)
    spec["github_token"] = os.environ["GITHUB_TOKEN_2"]
    with Request(spec) as req:
        yield req.results


@pytest.fixture
def error_results_with_github_token(private_repo_spec):
    spec = deepcopy(private_repo_spec)
    spec["github_token"] = "nonsense"
    with Request(spec) as req:
        yield req.results


def check_spec_in_results(spec, results):
    # check spec got copied into results
    for key, val in spec.items():
        assert results[key] == val


def get_error(results, key):
    check_spec_in_results(results["spec"], results["results"])
    error = results["results"]["error"]
    for (i, (msg, _)), status in zip_longest(enumerate(STATUS_MESSAGES), results["status"]):
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


def check_code_snippets_in_results(results):
    snippet_map = {
        "src/stories/Button.jsx": "import React from 'react';\n"
        "import PropTypes from "
        "'prop-types';\n"
        "import './button.css';\n"
        "\n"
        "/**\n",
        "src/stories/Button.stories.jsx": "import React from 'react';\n\n"
        "import { Button } from './Button';\n\n"
        "// More on default export: https://storybook.js.org/docs/react/writing-stories/introduction#default-export\n",
    }
    for (code_path, code_snippet) in zip(results["code_paths"], results["code_snippets"]):
        assert snippet_map[code_path] == code_snippet


def check_status_messages(success_results):
    for (i, (msg, keys)), status in zip_longest(
        enumerate(STATUS_MESSAGES), success_results["status"]
    ):
        assert status["step"] == i
        assert status["step_count"] == len(STATUS_MESSAGES)
        assert status["message"] == msg
        for key in keys:
            assert key in status["results"]


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
    check_code_snippets_in_results(results)
    code_size = results["code_size"]
    assert code_size > 2000000 and code_size < 5000000
    for key in ("check_frame", "gray_difference", "blue_difference", "screenshot"):
        check_url(results[f"url_{key}"])


@pytest.mark.skip(reason="in the interest of time")
def test_should_be_able_to_successfully_run_check2(success_results2):
    return test_should_be_able_to_successfully_run_check(success_results2)


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

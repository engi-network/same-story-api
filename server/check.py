import asyncio
import gettext
import json
import os
import socket
import sys
from ast import dump
from asyncio.subprocess import PIPE
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter, time
from tkinter import W

from helpful_scripts import setup_logging

log = setup_logging()

_ = gettext.gettext


@contextmanager
def set_directory(path):
    origin = Path().absolute()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(origin)


def get_port():
    sock = socket.socket()
    sock.bind(("", 0))
    return sock.getsockname()[1]


async def run(cmd):
    log.info(cmd)
    t1_start = perf_counter()
    proc = await asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    stdout = stdout.decode() if stdout else None
    stderr = stderr.decode() if stderr else None
    t1_stop = perf_counter()

    if stdout:
        log.info(f"[stdout]\n{stdout}")
    if stderr:
        log.info(f"[stderr]\n{stderr}")
    log.info(f"{cmd!r} exited with code {proc.returncode} elapsed {t1_stop - t1_start} seconds")
    return proc.returncode, stdout, stderr


class CheckError(Exception):
    messages = {
        "clone": _("failed to clone GitHub repo"),
        "frame": _("Figma frame missing (no such file)"),
        "branch": _("failed to sync GitHub repo, check branch"),
        "commit": _("failed to checkout commit in GitHub repo"),
        "install": _("npm install failed"),
        "storycap": _("storycap failed"),
        "aws": _("internal AWS error"),
        "comp": _("failed to generate visual comparison"),
    }

    def __init__(self, e_key, stdout=None, stderr=None):
        self.e_key = e_key
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        return CheckError.messages[self.e_key]

    def to_dict(self):
        return {
            "error": {
                self.e_key: str(self),
                "stdout": self.stdout,
                "stderr": self.stderr,
            }
        }


async def run_raise(cmd, returncode=0, e_key=None):
    returncode_, stdout, stderr = await run(cmd)
    if returncode_ != returncode:
        raise CheckError(e_key, stdout, stderr)
    return returncode_


def gettempdir():
    # return tempfile.gettempdir()
    return Path(os.environ.get("TMPDIR", "/tmp/"))


def get_dims(spec_d):
    # somehow the screenshot ends up being 2x the dimensions given below!
    height = int(spec_d.get("height", "600")) // 2
    width = int(spec_d.get("width", "800")) // 2
    return f"{width}x{height}"


async def check(spec_d):
    t1_start = perf_counter()
    prefix = Path(f"same-story/checks/{spec_d['check_id']}")
    check_dir = gettempdir() / prefix
    check_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"{prefix=} {check_dir=}")
    await run_raise(f"aws s3 cp s3://{prefix} {check_dir} --recursive", e_key="aws")
    check_repo = spec_d["repository"]
    code = check_dir / "code"
    results = "results.json"
    story = spec_d["story"]
    frame = check_dir / f"frames/{story}.png"
    branch = spec_d.get("branch")
    branch_cmd = f" --branch {branch}" if branch is not None else ""
    commit = spec_d.get("commit")
    github_token = spec_d.get("github_token")
    # TODO shouldn't really log this command
    github_cmd = f"GITHUB_TOKEN='{github_token}' gh" if github_token else "gh"
    sync = True
    try:
        if not frame.exists():
            raise CheckError("frame", stderr=str(frame))
        if not code.exists():
            sync = False
            await run_raise(f"{github_cmd} repo clone {check_repo} {code}", e_key="clone")
        with set_directory(code):
            if sync:
                # stash any local changes, e.g. package-lock.json
                await run_raise(f"git stash", e_key="clone")
            if branch:
                await run_raise(f"{github_cmd} repo sync{branch_cmd}", e_key="branch")
            if commit:
                await run_raise(f"git checkout {commit}", e_key="commit")
            await run_raise("npm install", e_key="install")

            log.info("capturing screenshots")
            # TODO storycap concurrency fail, hence MAX_QUEUE_MESSAGES=1
            port = get_port()
            await run_raise(
                f"npx storycap http://localhost:{port} --viewport {get_dims(spec_d)} "
                f"--serverCmd 'start-storybook -p {port}'",
                e_key="storycap",
            )

            log.info("uploading code screenshots to s3")
            await run_raise(
                f"aws s3 cp {code}/__screenshots__ "
                f"s3://{prefix}/report/__screenshots__ --recursive",
                e_key="aws",
            )

            log.info("running visual comparisons")
            screenshot = (
                code / f"__screenshots__/{spec_d['path']}/{spec_d['component']}/{story}.png"
            )
            if not screenshot.exists():
                raise CheckError("storycap", stderr=str(screenshot))

            log.info("running regression with blue hightlight and uploading")
            blue_difference = Path("blue_difference.png")
            # compare exits with code 1 even though it seems to have run successfully
            await run(
                f"compare '{screenshot}' '{frame}' " f"-highlight-color blue {blue_difference}"
            )
            if not blue_difference.exists():
                raise CheckError("comp", stderr=str(blue_difference))
            await run_raise(
                f"aws s3 cp {blue_difference} s3://{prefix}/report/{blue_difference}",
                e_key="aws",
            )

            log.info("running regression with gray hightlight and uploading")
            gray_difference = Path("gray_difference.png")
            await run_raise(
                f"convert '{screenshot}' -flatten -grayscale Rec709Luminance "
                f"'{frame}' -flatten -grayscale Rec709Luminance "
                "-clone 0-1 -compose darken -composite "
                f"-channel RGB -combine {gray_difference}",
                e_key="comp",
            )
            if not gray_difference.exists():
                raise CheckError("comp", stderr=str(gray_difference))
            await run_raise(
                f"aws s3 cp {gray_difference} s3://{prefix}/report/{gray_difference}",
                e_key="aws",
            )
            # compare exits with code 1 even though it seems to have run successfully
            _, _, stderr = await run(f"compare -metric MAE '{screenshot}' '{frame}' null")
            t1_stop = perf_counter()
            log.info(f"check done {t1_stop - t1_start} seconds")
            now = time()
            json.dump(
                {
                    **spec_d,
                    "MAE": stderr,
                    "created_at": now - t1_start,
                    "completed_at": now,
                },
                open(results, "w"),
            )
            await run_raise(f"aws s3 cp {results} s3://{prefix}/report/{results}", e_key="aws")
    except CheckError as e:
        log.exception(e)
        results_file = check_dir / results
        d = {**spec_d, **e.to_dict()}
        log.error(f"{d=}")
        json.dump(d, open(results_file, "w"))
        await run_raise(f"aws s3 cp {results_file} s3://{prefix}/report/{results}")

    return 0


async def main():
    await check(sys.argv[1])


if __name__ == "__main__":
    asyncio.run(main())

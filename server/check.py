import asyncio
import gettext
import json
import os
import re
import socket
import sys
from asyncio.subprocess import PIPE
from contextlib import contextmanager
from pathlib import Path
from shlex import quote
from time import perf_counter, time

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


async def run(cmd, log_cmd=None):
    if log_cmd is None:
        log_cmd = cmd
    # don't log env vars
    log_cmd = re.subn("\S+=\S+ ", "", log_cmd)[0]
    log.info(log_cmd)
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
    log.info(
        f"{log_cmd!r} exited with code {proc.returncode} elapsed {t1_stop - t1_start} seconds"
    )
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


async def run_seq(funcs):
    [await f() for f in funcs]


class CheckRequest(object):
    def __init__(self, spec_d):
        self.spec_d = spec_d
        self.prefix = Path(f"same-story/checks/{spec_d['check_id']}")
        self.check_dir = gettempdir() / self.prefix
        self.check_dir.mkdir(parents=True, exist_ok=True)

    async def download(self):
        await run_raise(f"aws s3 cp s3://{self.prefix} {self.check_dir} --recursive", e_key="aws")
        self.repo = self.spec_d["repository"]
        self.code = self.check_dir / "code"
        self.results = "results.json"
        self.story = self.spec_d["story"]
        self.frame = self.check_dir / f"frames/{self.story}.png"
        if not self.frame.exists():
            raise CheckError("frame", stderr=str(self.frame))

    async def run_git(self):
        github_token = quote(self.spec_d.get("github_token", os.environ["GITHUB_TOKEN"]))
        # don't ask 😆
        self.github_cmd = f"GITHUB_TOKEN='{github_token}' gh"
        github_opts = (
            f"-- -c url.'https://{github_token}:@github.com/'.insteadOf='https://github.com/'"
        )
        # oh, alright then -- the -c option lets us use the GitHub personal access
        # token as the Git credential helper
        if not self.code.exists():
            self.sync = False
            log_cmd = f"{self.github_cmd} repo clone {self.repo} {self.code}"
            await run_raise(
                f"{log_cmd} {github_opts}",
                e_key="clone",
                log_cmd=log_cmd,  # don't log secrets
            )
        else:
            self.sync = True

    async def sync_repo(self):
        branch = self.spec_d.get("branch")
        branch_cmd = f" --branch {branch}" if branch is not None else ""
        commit = self.spec_d.get("commit")
        if self.sync:
            # stash any local changes, e.g. package-lock.json
            await run_raise(f"git stash", e_key="clone")
        if branch:
            await run_raise(f"{self.github_cmd} repo sync{branch_cmd}", e_key="branch")
        if commit:
            await run_raise(f"git checkout {commit}", e_key="commit")

    async def install_packages(self):
        await run_raise("npm install", e_key="install")

    def get_dims(self):
        height = int(self.spec_d.get("height", "600"))
        width = int(self.spec_d.get("width", "800"))
        return f"{width}x{height}"

    async def run_storycap(self):
        port = get_port()
        await run_raise(
            f"npx storycap http://localhost:{port} --viewport {self.get_dims()} "
            f"--serverCmd 'start-storybook -p {port}'",
            e_key="storycap",
        )
        self.screenshot = (
            self.code
            / f"__screenshots__/{self.spec_d['path']}/{self.spec_d['component']}/{self.story}.png"
        )
        if not self.screenshot.exists():
            raise CheckError("storycap", stderr=str(self.screenshot))

    async def run_visual_comparisons(self):
        self.gray_difference = Path("gray_difference.png")
        await run_raise(
            f"convert '{self.screenshot}' -flatten -grayscale Rec709Luminance "
            f"'{self.frame}' -flatten -grayscale Rec709Luminance "
            "-clone 0-1 -compose darken -composite "
            f"-channel RGB -combine {self.gray_difference}",
            e_key="comp",
        )
        if not self.gray_difference.exists():
            raise CheckError("comp", stderr=str(self.gray_difference))

        self.blue_difference = Path("blue_difference.png")
        # compare exits with code 1 even though it seems to have run successfully
        await run(
            f"compare '{self.screenshot}' '{self.frame}' "
            f"-highlight-color blue {self.blue_difference}"
        )
        if not self.blue_difference.exists():
            raise CheckError("comp", stderr=str(self.blue_difference))

    async def run_numeric_comparisons(self):
        # compare exits with code 1 even though it seems to have run successfully
        _, _, self.mae = await run(f"compare -metric MAE '{self.screenshot}' '{self.frame}' null")

    async def upload(self):
        await run_raise(
            f"aws s3 cp {self.code}/__screenshots__ "
            f"s3://{self.prefix}/report/__screenshots__ --recursive",
            e_key="aws",
        )
        for f in self.blue_difference, self.gray_difference:
            await run_raise(
                f"aws s3 cp {f} s3://{self.prefix}/report/{f}",
                e_key="aws",
            )
        self.t1_stop = perf_counter()
        log.info(f"check done {self.t1_stop - self.t1_start} seconds")
        now = time()
        json.dump(
            {
                **self.spec_d,
                "MAE": self.mae,
                "created_at": now - self.t1_start,
                "completed_at": now,
            },
            open(self.results, "w"),
        )
        await run_raise(
            f"aws s3 cp {self.results} s3://{self.prefix}/report/{self.results}", e_key="aws"
        )

    async def run(self):
        try:
            self.t1_start = perf_counter()
            await run_seq([self.download, self.run_git])
            with set_directory(self.code):
                await run_seq(
                    [
                        self.sync_repo,
                        self.install_packages,
                        self.run_storycap,
                        self.run_visual_comparisons,
                        self.run_numeric_comparisons,
                        self.upload,
                    ]
                )
        except CheckError as e:
            log.exception(e)
            results_file = self.check_dir / self.results
            d = {**self.spec_d, **e.to_dict()}
            log.error(f"{d=}")
            json.dump(d, open(results_file, "w"))
            await run_raise(f"aws s3 cp {results_file} s3://{self.prefix}/report/{self.results}")


async def run_raise(cmd, returncode=0, e_key=None, log_cmd=None):
    returncode_, stdout, stderr = await run(cmd, log_cmd=log_cmd)
    if returncode_ != returncode:
        raise CheckError(e_key, stdout, stderr)
    return returncode_


def gettempdir():
    # return tempfile.gettempdir()
    return Path(os.environ.get("TMPDIR", "/tmp/"))


def get_dims(spec_d):
    height = int(spec_d.get("height", "600"))
    width = int(spec_d.get("width", "800"))
    return f"{width}x{height}"


async def main():
    await CheckRequest(sys.argv[1]).run()


if __name__ == "__main__":
    asyncio.run(main())

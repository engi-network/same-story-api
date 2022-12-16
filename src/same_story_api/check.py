import asyncio
import gettext
import json
import os
import sys
from pathlib import Path
from shlex import quote as sh_quote
from time import time
from urllib.parse import quote

from engi_helpful_scripts.git import (
    get_git_secrets,
    git_sync,
    github_checkout,
    is_git_secrets,
)
from engi_helpful_scripts.run import CmdError, run, set_directory
from helpful_scripts import cleanup_directory, get_port, get_s3_url, log, make_s3_public

_ = gettext.gettext


BUCKET_NAME = os.environ["BUCKET_NAME"]
NPM_REGISTRY = os.environ.get("NPM_REGISTRY")


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


def head(path, n=5):
    """Return the first n lines of file path"""
    snippet = ""
    with open(path) as fp:
        for _ in range(n):
            snippet += fp.readline()
    return snippet


def raise_or_return(cmd_exit, returncode=0, e_key=None):
    error = (
        CheckError(e_key, cmd_exit.stdout, cmd_exit.stderr)
        if returncode != cmd_exit.returncode
        else None
    )
    if error:
        raise error
    return cmd_exit.returncode


class CheckRequest(object):
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
    PUBLIC_ACL_ARGS = "--acl public-read"

    def __init__(self, spec_d, status_callback):
        log.info(f"{BUCKET_NAME=}")
        log.info(f"{NPM_REGISTRY=}")
        self.spec_d = spec_d
        self.results_d = {}
        self.status_callack = status_callback
        self.prefix = Path(f"{BUCKET_NAME}/checks/{spec_d['check_id']}")
        self.check_dir = gettempdir() / self.prefix
        self.check_dir.mkdir(parents=True, exist_ok=True)
        self.step = 0

    async def send_status(self, error=None):
        msg = {
            "check_id": self.spec_d["check_id"],
            "step": self.step,
            "step_count": len(self.STATUS_MESSAGES),
            "results": self.results_d,
        }
        if error:
            msg["error"] = error.to_dict()["error"]
        else:
            msg["message"] = self.STATUS_MESSAGES[self.step]
            self.step += 1
        await self.status_callack(msg)

    async def download(self):
        await self.run_raise(
            f"aws s3 cp s3://{self.prefix} {self.check_dir} --recursive", e_key="aws"
        )
        self.repo = self.spec_d["repository"]
        self.code = self.check_dir / "code"
        self.node_modules = self.code / "node_modules"
        self.results = "results.json"
        self.story = self.spec_d["story"]
        frame = f"frames/{self.story}.png"
        self.frame = self.check_dir / frame
        if self.frame.exists():
            frame_full = f"{self.prefix}/{frame}"
            error = None
            make_s3_public(frame_full)
            self.results_d["url_check_frame"] = get_s3_url(quote(frame_full))
        else:
            error = CheckError("frame", stderr=str(f"failed to download {frame}"))
        await self.send_status(error=error)
        if error:
            raise error

    async def run_git(self):
        github_token = sh_quote(self.spec_d.get("github_token", os.environ["GITHUB_TOKEN"]))
        if not self.code.exists():
            try:
                await github_checkout(self.repo, self.code, github_token=github_token)
            except CmdError as e:
                return raise_or_return(e.cmd_exit, e_key="clone")
        else:
            self.sync = True

    async def sync_repo(self):
        branch = self.spec_d.get("branch")
        commit = self.spec_d.get("commit")
        try:
            await git_sync(branch, commit)
        except CmdError as e:
            raise_or_return(e.cmd_exit, e_key="branch" if branch in e.cmd else "commit")
        self.get_code_snippets()
        self.get_code_size()
        await self.send_status()

    async def reveal_secrets(self):
        # if this repo contains git secrets reveal them
        if await is_git_secrets():
            await get_git_secrets()

    def get_code_snippets(self):
        code_snippets = []
        code_paths = []
        # the storybook source might be a .jsx or .tsx file
        for p in Path("./").rglob(f"*/{self.spec_d['component']}*.[jt]sx"):
            code_paths.append(str(p))
            code_snippets.append(head(p))

        self.results_d.update({"code_paths": code_paths, "code_snippets": code_snippets})

    def get_code_size(self):
        self.results_d["code_size"] = sum(
            f.stat().st_size for f in self.code.glob("**/*") if f.is_file()
        )

    async def install_packages(self):
        if NPM_REGISTRY is not None:
            await self.run_raise(f"npm set registry {NPM_REGISTRY}", e_key="install")
        await self.run_raise("npm install", e_key="install")
        await self.send_status()

    def get_dims(self):
        height = int(self.spec_d.get("height", "600"))
        width = int(self.spec_d.get("width", "800"))
        return f"--viewport {width}x{height}"

    def get_query(self):
        def get(key):
            return self.spec_d[key].lower().replace(" ", "-").replace("/", "-")

        args = self.spec_d.get("args")
        return "--additionalQuery 'path=/story/{path}-{component}--{story}{args}'".format(
            path=get("path"),
            component=get("component"),
            story=get("story"),
            args="&args={}".format(
                ";".join([f"{val['name']}:{val['value']}" for val in args.values()])
            )
            if args
            else "",
        )

    def get_include(self, quote=quote):
        story = quote(self.story)
        return f"{self.spec_d['path']}/{self.spec_d['component']}/{story}"

    def get_story_include(self):
        return f"--include '{self.get_include(quote=lambda x: x)}'"

    def get_timeout(self):
        server_timeout = int(self.spec_d.get("server_timeout", 50_000))
        capture_timeout = int(self.spec_d.get("capture_timeout", 10_000))
        return f"--serverTimeout {server_timeout} --captureTimeout {capture_timeout} "

    def get_screenshot(self, quote=quote):
        return f"__screenshots__/{self.get_include(quote=quote)}.png"

    async def run_storycap(self):
        port = get_port()
        await self.run_raise(
            f"npx storycap http://localhost:{port} {self.get_dims()} {self.get_timeout()} "
            f"{self.get_query()} {self.get_story_include()} --serverCmd 'start-storybook -p {port}'",
            e_key="storycap",
        )

        screenshot = self.get_screenshot(quote=lambda x: x)
        self.screenshot = self.code / screenshot
        if not self.screenshot.exists():
            raise CheckError(
                "storycap",
                stderr=f"storycap ran successfully but expected screenshot {screenshot} wasn't created",
            )

        await self.run_raise(
            f"aws s3 cp {self.code}/__screenshots__ "
            f"s3://{self.prefix}/report/__screenshots__ --recursive {self.PUBLIC_ACL_ARGS}",
            e_key="aws",
        )
        self.results_d["url_screenshot"] = self.get_url(self.get_screenshot())

        await self.send_status()

    async def run_visual_comparisons(self):
        self.gray_difference = Path("gray_difference.png")
        await self.run_raise(
            f"convert '{self.screenshot}' -flatten -grayscale Rec709Luminance "
            f"'{self.frame}' -flatten -grayscale Rec709Luminance "
            "-clone 0-1 -compose darken -composite "
            f"-channel RGB -combine {self.gray_difference}",
            e_key="comp",
        )
        error = (
            None
            if self.gray_difference.exists()
            else CheckError("comp", stderr=str(self.gray_difference))
        )
        if error:
            await self.send_status(error=error)
            raise error

        self.blue_difference = Path("blue_difference.png")
        # compare exits with code 1 even though it seems to have run successfully
        await run(
            f"compare '{self.screenshot}' '{self.frame}' "
            f"-highlight-color blue {self.blue_difference}",
            raise_code=None,
        )
        error = (
            None
            if self.blue_difference.exists()
            else CheckError("comp", stderr=str(self.blue_difference))
        )
        if error:
            await self.send_status(error=error)
            raise error

        for f in self.blue_difference, self.gray_difference:
            key = str(f).split(".")[0]
            await self.run_raise(
                f"aws s3 cp {f} s3://{self.prefix}/report/{f} {self.PUBLIC_ACL_ARGS}",
                e_key="aws",
            )
            self.results_d[f"url_{key}"] = self.get_url(f)

        await self.send_status()

    async def run_numeric_comparisons(self):
        # compare exits with code 1 even though it seems to have run successfully
        cmd_exit = await run(
            f"compare -metric MAE '{self.screenshot}' '{self.frame}' null", raise_code=None
        )
        self.results_d["MAE"] = cmd_exit.stderr.strip()
        await self.send_status()

    def get_url(self, path_quoted):
        return get_s3_url(f"{self.prefix}/report/{path_quoted}")

    async def upload(self):
        self.results_d["completed_at"] = time()
        duration = self.results_d["completed_at"] - self.results_d["created_at"]
        log.info(f"check done {duration} seconds")
        json.dump(
            {
                **self.spec_d,
                **self.results_d,
            },
            open(self.results, "w"),
        )
        await self.run_raise(
            f"aws s3 cp {self.results} s3://{self.prefix}/report/{self.results}", e_key="aws"
        )
        await self.send_status()

    async def df(self):
        await self.run_raise(f"df -h {gettempdir()}")

    async def run(self):
        try:
            self.results_d["created_at"] = time()
            await run_seq([self.df, self.send_status, self.download, self.run_git])
            with set_directory(self.code):
                # delete the node_modules directory; it's too big to persist
                with cleanup_directory(self.node_modules):
                    await run_seq(
                        [
                            self.sync_repo,
                            self.reveal_secrets,
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
            await self.run_raise(
                f"aws s3 cp {results_file} s3://{self.prefix}/report/{self.results}"
            )
            await self.send_status(error=e)

    async def run_raise(self, cmd, returncode=0, e_key=None, log_cmd=None):
        cmd_exit = await run(cmd, log_cmd=log_cmd, raise_code=None)
        return raise_or_return(cmd_exit, returncode, e_key)


def gettempdir():
    # return tempfile.gettempdir()
    return Path(os.environ.get("TMPDIR", "/tmp/"))


async def main():
    await CheckRequest(sys.argv[1]).run()


if __name__ == "__main__":
    asyncio.run(main())

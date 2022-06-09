import os

if __name__ == "__main__":
    for key in [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
        "GITHUB_TOKEN",
        "GITHUB_TOKEN_2",
    ]:
        val = os.environ[key]
        print(f"{key}={val}")

import os


class PipelineError(Exception):
    pass


def write_parquet(df, path: str) -> None:
    temp_path = path + ".tmp"
    df.to_parquet(temp_path, index=False)
    os.replace(temp_path, path)


def git_revision() -> str:
    """The code revision to record in run.json.

    Three ways of asking, in order of how much we trust the answer:

    1. git in the current directory. This works on the host.
    2. git using the repo directory that compose mounts at /repo/.git. This is
       how it works inside the container, where /workspace is only the starter
       folder and git would otherwise exit non-zero and leave us writing
       "unknown".
    3. the stamped .git-revision file. Nothing updates this automatically, so
       it can be out of date and it is only a last resort.
    """
    import subprocess

    commands = (
        ["git", "rev-parse", "HEAD"],
        ["git", "--git-dir=/repo/.git", "rev-parse", "HEAD"],
    )
    for command in commands:
        try:
            revision = subprocess.check_output(
                command, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            continue
        if revision:
            return revision

    for candidate in (".git-revision", "../.git-revision"):
        if os.path.isfile(candidate):
            with open(candidate) as handle:
                revision = handle.read().strip()
            if revision:
                return revision

    return "unknown"

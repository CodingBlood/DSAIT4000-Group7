import os


class PipelineError(Exception):
    pass


def write_parquet(df, path: str) -> None:
    temp_path = path + ".tmp"
    df.to_parquet(temp_path, index=False)
    os.replace(temp_path, path)

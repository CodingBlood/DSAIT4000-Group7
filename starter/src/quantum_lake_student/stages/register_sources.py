"""Register and verify the original source files.

Student responsibilities:

- verify checksums from the release description;
- list source files and archive members safely;
- keep the supplied bytes unchanged;
- report missing or unexpected source objects;
- make a second run safe: no duplicate source records.
"""
import hashlib
import io
import json
import os
import zipfile

from quantum_lake_student.config import Settings
from quantum_lake_student.connections import minio_client, bronze_inventory
from quantum_lake_student.models import StageResult
from quantum_lake_student.pipeline_utils import PipelineError

def load_expected_manifest() -> dict:
    manifest_path = "/course-data/metadata/bundle-manifest.json"
    if not os.path.exists(manifest_path):
        raise PipelineError(f"Release manifest {manifest_path} not found. Check that the course data is mounted.")
    with open(manifest_path) as f:
        manifest = json.load(f)
    return {
        obj["path"].removeprefix("raw/"): obj
        for obj in manifest["objects"]
    }


def is_safe_member(member_name: str) -> bool:
    normalized = os.path.normpath(member_name)
    return not normalized.startswith("..") and not os.path.isabs(normalized)


def run(run_id: str) -> StageResult:
    result = StageResult(stage="register_sources", run_id=run_id)
    settings = Settings.from_environment()
    client = minio_client(settings)

    expected = load_expected_manifest()
    expected_keys = {f"bronze/{path}" for path in expected}
    actual_keys = {key for key, _size in bronze_inventory(settings)}

    missing = expected_keys - actual_keys
    unexpected = actual_keys - expected_keys

    if missing:
        raise PipelineError(f"Missing expected Bronze objects: {sorted(missing)}. Run make seed to load them.")
    if unexpected:
        print(f"Warning: unexpected objects in Bronze, not in manifest: {unexpected}")

    registry_rows = []
    for path, info in expected.items():
        key = f"bronze/{path}"
        response = client.get_object(settings.s3_bucket, key)
        raw_bytes = response.read()
        response.close()
        response.release_conn()

        actual_hash = hashlib.sha256(raw_bytes).hexdigest()
        actual_size = len(raw_bytes)

        if actual_hash != info["sha256"]:
            raise PipelineError(
                f"Bronze object {key} is changed or damaged: "
                f"expected sha256 {info['sha256']}, got {actual_hash}"
            )
        if actual_size != info["bytes"]:
            raise PipelineError(
                f"Bronze object {key} has the wrong size: "
                f"expected {info['bytes']} bytes, got {actual_size}"
            )

        try:
            archive = zipfile.ZipFile(io.BytesIO(raw_bytes))
        except zipfile.BadZipFile:
            raise PipelineError(f"Bronze object {key} is not a readable zip archive")
        with archive as z:
            unsafe_members = [n for n in z.namelist() if not is_safe_member(n)]
            if unsafe_members:
                raise PipelineError(f"Unsafe archive member paths in {key}: {unsafe_members}")
            member_count = len(z.namelist())

        registry_rows.append({
            "source_name": info["source"],
            "bronze_object": key,
            "sha256": actual_hash,
            "bytes": actual_size,
            "member_count": member_count,
            "mandatory": info["mandatory"],
        })
        print(f"OK: {key}: checksum and size verified, {member_count} archive members, all paths safe")

    input_hashes = {}
    for row in registry_rows:
        input_hashes[row["bronze_object"]] = {"sha256": row["sha256"], "bytes": row["bytes"]}
    result.input_hashes = input_hashes
    result.input_count = len(expected)
    result.output_count = len(registry_rows)
    result.issue_count = 0
    result.finish()
    return result
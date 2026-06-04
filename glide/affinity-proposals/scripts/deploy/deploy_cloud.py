from __future__ import annotations

import argparse
import base64
import io
import zipfile
from pathlib import Path

from scripts.seren_client import GatewayClient


EXCLUDE_NAMES = {".env", "config.json", ".venv", "__pycache__", ".pytest_cache", "out", "state"}


def build_bundle(skill_root: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in skill_root.rglob("*"):
            if any(part in EXCLUDE_NAMES for part in path.parts):
                continue
            if path.suffix == ".pdf":
                continue
            if path.is_file():
                archive.write(path, path.relative_to(skill_root))
    return buffer.getvalue()


def deploy(skill_root: Path, *, name: str) -> dict:
    gateway = GatewayClient.from_env(skill_root=skill_root)
    bundle = base64.b64encode(build_bundle(skill_root)).decode("ascii")
    return gateway.call_publisher(
        "seren-cloud",
        method="POST",
        path="/deploy",
        body={
            "name": name,
            "skill_slug": "glide-affinity-proposals",
            "mode": "cron",
            "cron_schedule": "0 10 * * *",
            "cron_timezone": "America/Chicago",
            "code_bundle_base64": bundle,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--name", default="glide-affinity-proposals")
    args = parser.parse_args()
    result = deploy(Path(args.skill_root), name=args.name)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

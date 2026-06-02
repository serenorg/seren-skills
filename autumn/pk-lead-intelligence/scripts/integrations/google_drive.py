"""Google Drive upload + share for the weekly status doc (Phase 4).

Wraps two `google-drive` publisher calls: `POST /files` to upload
the rendered doc into the configured folder, then
`POST /files/{file_id}/permissions` to grant read access to
`nathan_share_email`. The publisher call is injected so unit
tests can exercise the dispatch without hitting the network.

The share-gate is load-bearing: a missing `nathan_share_email`
must not silently publish the doc into a folder no one watches.
The function returns `skipped_no_email` in that case so the
daily summary surfaces the misconfiguration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from scripts.output.weekly_status import WeeklyStatusDoc


# --------------------------------------------------------------------- #
# Types                                                                 #
# --------------------------------------------------------------------- #


# Signature of the publisher caller the agent passes in. Returning a
# `dict` keeps the seam transport-agnostic — production wires this
# to `seren_client.call_publisher`; tests pass a closure.
PublisherCall = Callable[[str, str, dict], dict]


@dataclass(frozen=True)
class ShareResult:
    """Outcome of one `upload_and_share` call.

    `status` is one of:

    * `shared` — doc uploaded and the share permission was granted.
    * `skipped_no_email` — `nathan_share_email` was empty; the doc
      was not uploaded.
    * `dry_run` — caller passed `dry_run=True`; the function
      surfaced what it would have done without calling the
      publisher.

    `doc_url` is the `webViewLink` returned by the upload call when
    `status='shared'`, otherwise None.
    """

    status: str
    doc_url: Optional[str]
    shared_with: Optional[str]


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def create_folder(
    *,
    name: str,
    publisher_call: PublisherCall,
) -> str:
    """Create a Google Drive folder and return its id.

    Used by `scripts.bootstrap` to provision the weekly-reports
    output folder on first run. Kept distinct from `upload_and_share`
    because folder creation has different idempotency rules — Drive
    silently allows duplicate folder names, and the chat AI calls
    this once per fresh install, not per-run.
    """

    resp = publisher_call(
        "google-drive",
        "/files",
        {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    folder_id = resp.get("id")
    if not folder_id:
        raise RuntimeError(
            "google-drive POST /files for folder returned no `id`. "
            f"Response keys: {sorted(resp.keys())}"
        )
    return folder_id


def upload_and_share(
    *,
    doc: WeeklyStatusDoc,
    folder_id: str,
    share_email: str,
    dry_run: bool,
    publisher_call: PublisherCall,
) -> ShareResult:
    """Upload `doc` to Drive and share with `share_email`.

    Empty `share_email` short-circuits to `skipped_no_email` before
    any publisher call — the operator must see the misconfiguration
    in the daily summary rather than have the doc silently land in
    a folder no one watches.

    Upload precedes share: a share against an unuploaded file id
    404s and forces the operator to chase the failure manually.
    """

    if not share_email:
        return ShareResult(
            status="skipped_no_email",
            doc_url=None,
            shared_with=None,
        )

    if dry_run:
        return ShareResult(
            status="dry_run",
            doc_url=None,
            shared_with=share_email,
        )

    upload_body: dict[str, Any] = {
        "name": doc.title,
        "parents": [folder_id],
        "mimeType": "application/vnd.google-apps.document",
        "body": doc.body,
    }
    upload_resp = publisher_call("google-drive", "/files", upload_body)
    file_id = upload_resp.get("id")
    doc_url = upload_resp.get("webViewLink")
    if not file_id:
        raise RuntimeError(
            "google-drive POST /files returned no `id`; cannot share. "
            f"Response keys: {sorted(upload_resp.keys())}"
        )

    publisher_call(
        "google-drive",
        f"/files/{file_id}/permissions",
        {
            "type": "user",
            "role": "reader",
            "emailAddress": share_email,
        },
    )

    return ShareResult(
        status="shared",
        doc_url=doc_url,
        shared_with=share_email,
    )

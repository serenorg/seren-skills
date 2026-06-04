from __future__ import annotations

import re
import tempfile
import base64
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.extract import ProposalProfile
from scripts.seren_client import PublisherError


class SetupBlocked(RuntimeError):
    """Raised when an operator setup prerequisite blocks a dry-run."""


@dataclass
class ProposalTemplatePaths:
    offshore: Path
    onshore: Path


@dataclass
class ProposalArtifact:
    pptx_path: Path | None
    pdf_bytes: bytes
    file_name: str
    template_used: Path | None


def _presentation_class():
    from pptx import Presentation

    return Presentation


def _format_month_year(day: date) -> str:
    return day.strftime("%B %Y")


def _format_long_date(day: date) -> str:
    return day.strftime("%B %d, %Y").replace(" 0", " ")


def _safe_filename(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return value or "proposal"


def _replacement_map(profile: ProposalProfile, today: date) -> list[tuple[str, str]]:
    launch_date = today + timedelta(days=30)
    return [
        ("Secured Debt Investments", profile.client_name),
        ("Secured Debt", profile.client_name),
        ("CLIENT_NAME", profile.client_name),
        ("FUND_NAME", profile.fund_name),
        ("ADVISOR_NAME", profile.advisor_name),
        ("DESCRIPTION", profile.description),
        ("SEEKING", "\n".join(profile.seeking)),
        ("CURRENT_MONTH_YEAR", _format_month_year(today)),
        ("LAUNCH_DATE", _format_long_date(launch_date)),
    ]


def _replace_paragraph_text(paragraph: Any, replacements: list[tuple[str, str]]) -> None:
    original = "".join(run.text for run in paragraph.runs)
    if not original:
        return
    changed = original
    for old, new in replacements:
        changed = changed.replace(old, new)
    if changed == original:
        return
    if not paragraph.runs:
        paragraph.text = changed
        return
    paragraph.runs[0].text = changed
    for run in paragraph.runs[1:]:
        run.text = ""


def _iter_text_frames(slide: Any):
    for shape in slide.shapes:
        if getattr(shape, "has_text_frame", False):
            yield shape.text_frame
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    yield cell.text_frame


def write_proposal_deck(
    profile: ProposalProfile,
    *,
    templates: ProposalTemplatePaths,
    output_path: Path,
    today: date,
) -> ProposalArtifact:
    template = templates.onshore if profile.structure == "onshore" else templates.offshore
    prs = _presentation_class()(str(template))
    replacements = _replacement_map(profile, today)
    for index, slide in enumerate(prs.slides):
        if index == 9:
            continue
        for text_frame in _iter_text_frames(slide):
            for paragraph in text_frame.paragraphs:
                _replace_paragraph_text(paragraph, replacements)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output_path)
    file_name = f"{_safe_filename(profile.client_name)}_proposal_{today:%Y%m%d}.pdf"
    return ProposalArtifact(
        pptx_path=output_path,
        pdf_bytes=b"",
        file_name=file_name,
        template_used=template,
    )


def extract_text_by_slide(path: Path) -> list[str]:
    prs = _presentation_class()(str(path))
    slides: list[str] = []
    for slide in prs.slides:
        chunks: list[str] = []
        for text_frame in _iter_text_frames(slide):
            text = "\n".join(paragraph.text for paragraph in text_frame.paragraphs)
            if text:
                chunks.append(text)
        slides.append("\n".join(chunks))
    return slides


class SharePointRenderer:
    def __init__(self, gateway: Any, *, folder_name: str = "AI Proposals") -> None:
        self.gateway = gateway
        self.folder_name = folder_name

    def preflight(self) -> dict[str, Any]:
        try:
            site = self.gateway.call_tool("microsoft-sharepoint", "get_sites_root", {})
        except PublisherError as exc:
            if "OAuthRequired" in str(exc):
                raise SetupBlocked(
                    "Microsoft OAuth connection required for the SharePoint render account. "
                    "Connect the render account to the microsoft-sharepoint publisher before dry-run."
                ) from exc
            raise
        return site

    def render_pdf(self, pptx_path: Path) -> bytes:
        site = self.preflight()
        site_id = site.get("id") or site.get("site", {}).get("id")
        if not site_id:
            raise RuntimeError("SharePoint root site response missing id")
        drive = self.gateway.call_tool(
            "microsoft-sharepoint",
            "get_sites_by_siteId_drive",
            {"siteId": site_id},
        )
        drive_id = drive.get("id") or drive.get("drive", {}).get("id")
        if not drive_id:
            raise RuntimeError("SharePoint drive response missing id")
        upload = self.gateway.call_tool(
            "microsoft-sharepoint",
            "put_drives_by_driveId_root___",
            {
                "driveId": drive_id,
                "body": {
                    "path": f"/{self.folder_name}/{pptx_path.name}",
                    "content_base64": base64.b64encode(pptx_path.read_bytes()).decode("ascii"),
                },
            },
        )
        item_id = upload.get("id") or upload.get("item", {}).get("id")
        if not item_id:
            raise RuntimeError("SharePoint upload response missing item id")
        pdf = self.gateway.call_publisher(
            "microsoft-sharepoint",
            method="GET",
            path=f"/drives/{drive_id}/items/{item_id}/content?format=pdf",
            response_format="bytes",
        )
        if isinstance(pdf, str):
            pdf_bytes = pdf.encode("latin1")
        else:
            pdf_bytes = bytes(pdf)
        if not pdf_bytes.startswith(b"%PDF"):
            raise RuntimeError("SharePoint render did not return PDF bytes")
        return pdf_bytes


class ProposalService:
    def __init__(
        self,
        *,
        templates: ProposalTemplatePaths,
        renderer: SharePointRenderer,
        output_dir: Path,
    ) -> None:
        self.templates = templates
        self.renderer = renderer
        self.output_dir = output_dir

    def build(self, profile: ProposalProfile, today: date) -> ProposalArtifact:
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp:
            pptx_path = Path(tmp.name)
        artifact = write_proposal_deck(
            profile,
            templates=self.templates,
            output_path=pptx_path,
            today=today,
        )
        pdf_bytes = self.renderer.render_pdf(pptx_path)
        out_path = self.output_dir / artifact.file_name
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(pdf_bytes)
        return ProposalArtifact(
            pptx_path=pptx_path,
            pdf_bytes=pdf_bytes,
            file_name=artifact.file_name,
            template_used=artifact.template_used,
        )

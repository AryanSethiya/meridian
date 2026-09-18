"""Parse inbound .eml files into a structured representation."""

from __future__ import annotations

from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes
    saved_path: Path | None = None


@dataclass
class ParsedEmail:
    path: Path
    email_id: str
    from_addr: str
    to_addr: str
    subject: str
    date: str
    body: str
    attachments: list[Attachment] = field(default_factory=list)


def _decode_payload(part: EmailMessage) -> bytes:
    payload = part.get_payload(decode=True)
    if payload is None:
        return b""
    return payload


def _extract_body(msg: EmailMessage) -> str:
    body_part = msg.get_body(preferencelist=("plain", "html"))
    if body_part is None:
        if not msg.is_multipart():
            content = msg.get_content()
            return content if isinstance(content, str) else ""
        return ""
    content = body_part.get_content()
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return content or ""


def parse_eml(path: Path, attachment_dir: Path | None = None) -> ParsedEmail:
    """Parse an RFC 822 .eml file and optionally write attachments to disk."""
    path = Path(path)
    with path.open("rb") as fh:
        msg = BytesParser(policy=policy.default).parse(fh)

    email_id = path.stem
    attachments: list[Attachment] = []

    if attachment_dir is not None:
        attachment_dir.mkdir(parents=True, exist_ok=True)

    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        data = _decode_payload(part)
        saved: Path | None = None
        if attachment_dir is not None:
            # Keep original basename; avoid path traversal.
            safe_name = Path(filename).name
            saved = attachment_dir / safe_name
            saved.write_bytes(data)
        attachments.append(
            Attachment(
                filename=Path(filename).name,
                content_type=part.get_content_type(),
                data=data,
                saved_path=saved,
            )
        )

    return ParsedEmail(
        path=path,
        email_id=email_id,
        from_addr=str(msg.get("From", "")),
        to_addr=str(msg.get("To", "")),
        subject=str(msg.get("Subject", "")),
        date=str(msg.get("Date", "")),
        body=_extract_body(msg).strip(),
        attachments=attachments,
    )

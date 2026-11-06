"""Read-only PR Brain queue projection and explicit Codex navigation actions."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

CONTROLLER_ID = os.environ.get("REVIEWGRID_CONTROLLER", "")
DEFAULT_STATE = Path(os.environ.get("REVIEWGRID_STATE", Path.home() / ".config" / "reviewgrid" / "state.json")).expanduser()
KEY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*\Z")
STATUSES = {
    "awaiting-approval": (0, "Ready for you", "#ff83cf"),
    "clean": (0, "Ready to approve", "#ff83cf"),
    "failed": (1, "Needs attention", "#ffb36b"),
    "stuck": (1, "Needs attention", "#ffb36b"),
    "reviewing": (2, "Reviewing", "#5be3ee"),
    "card": (3, "Queued", "#baa0fb"),
    "pending": (3, "Queued", "#baa0fb"),
    "stale": (3, "Head changed", "#ffb36b"),
    "posted": (4, "Watching author updates", "#a2abbc"),
}


@dataclass(frozen=True)
class Review:
    key: str
    title: str
    status: str
    session_id: str
    head_sha: str
    followup: bool

    @property
    def label(self) -> str:
        return STATUSES[self.status][1]

    @property
    def color(self) -> str:
        return STATUSES[self.status][2]


def load_reviews(path: Path = DEFAULT_STATE) -> list[Review]:
    if path.stat().st_size > 2_000_000:
        raise ValueError("Queue exceeds the widget's size limit")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("prs"), dict):
        raise ValueError("Queue must contain a prs object")
    rows = []
    for key, record in data["prs"].items():
        if not KEY.fullmatch(key) or not isinstance(record, dict):
            continue
        status = record.get("status")
        if status == "archived" and record.get("archived_from") == "posted":
            status = "posted"
        if not isinstance(status, str) or status not in STATUSES:
            continue
        rows.append(Review(key, _text(record.get("title")) or key, status,
                           _text(record.get("session_id")), _text(record.get("head_sha")),
                           record.get("kind") == "followup"))
    return sorted(rows, key=lambda row: (STATUSES[row.status][0], row.key.casefold()))


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _thread_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        return ""


def build_action(review: Review, controller_id: str = CONTROLLER_ID) -> tuple[str, str]:
    """Return an existing-chat link and a prompt; this action never submits a message."""
    ready = review.status in {"awaiting-approval", "clean"}
    controller = _thread_id(controller_id)
    destination = controller if ready else (_thread_id(review.session_id) or controller)
    if not destination:
        raise ValueError("Set --controller-thread to your Codex controller chat UUID.")
    context = f"{review.key} at head {review.head_sha or 'recorded in the queue'}"
    if ready:
        prompt = f"For {context}, show the exact drafted comments or clean approval offer here for my review."
    else:
        prompt = f"Show the current review status and next action for {context}. Reuse the existing review."
    return f"codex://threads/{destination}", prompt

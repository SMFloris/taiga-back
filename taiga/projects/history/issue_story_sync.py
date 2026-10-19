# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Mirror new issue/story activity without generating another model change."""
import re

from taiga.events.events import emit_event_for_model
from taiga.mdrender.service import render

from .choices import HistoryType
from .models import HistoryEntry


def mirror_issue_story_activity(obj, entry):
    if entry.type == HistoryType.delete:
        return

    model = obj._meta.label_lower
    if model == "issues.issue":
        targets = obj.generated_user_stories.filter(project_id=obj.project_id)
        kind, route = "issue", "issue"
    elif model == "userstories.userstory" and obj.generated_from_issue_id:
        targets = [obj.generated_from_issue] if obj.generated_from_issue.project_id == obj.project_id else []
        kind, route = "user story", "us"
    else:
        return

    # Creation assigns an initial status; it isn't a status transition.
    previous = getattr(obj, "prev", None)
    first_status_change = (entry.type == HistoryType.create and previous is not None
                           and previous.status_id != obj.status_id)
    status_change = first_status_change or (entry.type == HistoryType.change and "status" in entry.diff)
    if not entry.comment and not status_change:
        return

    from .services import make_key_from_model_object

    actor = (entry.user or {}).get("name") or "Unknown user"
    # Keep author names from injecting Markdown/HTML into the attribution.
    actor = re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", actor)
    source = f"[{kind} #{obj.ref}](/project/{obj.project.slug}/{route}/{obj.ref})"
    messages = []
    if entry.comment:
        quoted = "\n".join("> " + line for line in entry.comment.split("\n"))
        messages.append(f"{actor} commented on {source}:\n\n{quoted}")
    if status_change:
        if first_status_change:
            before = previous.status.name if previous.status else None
            after = obj.status.name if obj.status else None
        else:
            before, after = entry.values_diff["status"]
        messages.append(f"{actor} changed the status of {source} from **{before or 'None'}** to **{after or 'None'}**.")
    comment = "\n\n".join(messages)

    for target in targets:
        # Pure comment: never snapshot the target or replay its pending changes.
        # Writing directly also prevents mirrors from bouncing back to the source.
        HistoryEntry.objects.create(
            project_id=target.project_id,
            key=make_key_from_model_object(target),
            type=HistoryType.change,
            user={"pk": None, "name": "Issue Bot"},
            diff={}, values={}, snapshot=None,
            comment=comment, comment_html=render(target.project, comment),
            is_hidden=False, is_snapshot=False,
        )
        emit_event_for_model(target)

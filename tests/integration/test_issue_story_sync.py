import pytest
from django.urls import reverse

from taiga.base.utils import json
from taiga.projects.history.models import HistoryEntry
from taiga.projects.history.services import make_key_from_model_object, take_snapshot
from taiga.projects.userstories.models import UserStory
from .. import factories as f

pytestmark = pytest.mark.django_db


def comments(item):
    return HistoryEntry.objects.filter(key=make_key_from_model_object(item)).exclude(comment="")


def promoted_pair(client):
    project = f.create_project()
    f.MembershipFactory(project=project, user=project.owner, is_admin=True)
    client.login(project.owner)
    issue = f.IssueFactory(project=project, owner=project.owner, milestone=None,
                           status=project.default_issue_status, severity=project.default_severity,
                           priority=project.default_priority, type=project.default_issue_type)
    take_snapshot(issue, user=project.owner)
    response = client.json.post(reverse("issues-promote-to-user-story", args=[issue.pk]),
                                json.dumps({"project_id": project.pk}))
    assert response.status_code == 200
    story = UserStory.objects.get(project=project, ref=response.data[0])
    assert story.generated_from_issue_id == issue.pk
    return project, issue, story


@pytest.mark.parametrize("direction", ["issue", "story"])
@pytest.mark.parametrize("action", ["comment", "status", "both"])
def test_promoted_activity_is_mirrored_once(client, direction, action):
    project, issue, story = promoted_pair(client)
    source, target, endpoint = (issue, story, "issues-detail") if direction == "issue" else (story, issue, "userstories-detail")
    before = comments(target).count()
    data = {"version": source.version}
    text = "A **formatted** comment\n\nSecond paragraph <script>alert(1)</script>"
    if action in ("comment", "both"):
        data["comment"] = text
    if action in ("status", "both"):
        factory = f.IssueStatusFactory if direction == "issue" else f.UserStoryStatusFactory
        new_status = factory(project=project, name="Reviewed")
        data["status"] = new_status.pk
    original_target_status = target.status_id
    response = client.json.patch(reverse(endpoint, args=[source.pk]), json.dumps(data))
    assert response.status_code == 200, response.data
    mirrored = comments(target).order_by("created_at").last()
    assert comments(target).count() == before + 1
    assert mirrored.user == {"pk": None, "name": "Issue Bot"}
    assert project.owner.get_full_name() in mirrored.comment
    assert f"#{source.ref}](/project/{project.slug}/" in mirrored.comment
    assert mirrored.diff == {}
    assert "<script>" not in mirrored.comment_html
    if action in ("comment", "both"):
        assert "commented on" in mirrored.comment
        assert "> A **formatted** comment" in mirrored.comment
        assert "<strong>formatted</strong>" in mirrored.comment_html
    if action in ("status", "both"):
        assert "changed the status" in mirrored.comment
        assert source.status.name in mirrored.comment
        assert "Reviewed" in mirrored.comment
    assert not comments(source).filter(user__contains={"name": "Issue Bot"}).exists()
    target.refresh_from_db()
    assert target.status_id == original_target_status
    # Normal history endpoint renders the synthetic bot without a user account.
    history = client.get(reverse("userstory-history-detail" if direction == "issue" else "issue-history-detail", args=[target.pk]))
    assert history.status_code == 200
    assert any(e["user"]["name"] == "Issue Bot" for e in history.data)


def test_unrelated_updates_and_noops_do_not_mirror(client):
    project, issue, story = promoted_pair(client)
    baseline = comments(story).count()
    issue.subject = "New title"
    issue.save()
    take_snapshot(issue, user=project.owner)
    take_snapshot(issue, user=project.owner)
    assert comments(story).count() == baseline
    take_snapshot(issue, user=project.owner, delete=True)
    assert comments(story).count() == baseline


def test_issue_fans_out_without_story_updates_reaching_siblings(client):
    project, issue, story = promoted_pair(client)
    sibling = f.UserStoryFactory(project=project, generated_from_issue=issue)
    take_snapshot(sibling, user=project.owner)
    take_snapshot(issue, user=project.owner, comment="Issue update")
    assert comments(story).count() == 1
    assert comments(sibling).count() == 1
    take_snapshot(story, user=project.owner, comment="Story update")
    assert comments(issue).count() == 2
    assert comments(sibling).count() == 1


def test_cross_project_link_does_not_leak_activity(client):
    project, issue, story = promoted_pair(client)
    story.project = f.create_project()
    story.save()
    take_snapshot(issue, user=project.owner, comment="Private issue comment")
    take_snapshot(story, user=project.owner, comment="Private story comment")
    assert not comments(story).filter(user__contains={"name": "Issue Bot"}).exists()
    assert not comments(issue).filter(user__contains={"name": "Issue Bot"}).exists()


def test_promotion_copies_comments_without_copying_issue_status_diffs(client):
    project, issue, story = promoted_pair(client)
    take_snapshot(issue, user=project.owner, comment="Historical comment")
    result = client.json.post(reverse("issues-promote-to-user-story", args=[issue.pk]),
                              json.dumps({"project_id": project.pk}))
    second = UserStory.objects.get(project=project, ref=result.data[0])
    imported = comments(second).get()
    assert imported.comment == "Historical comment"
    assert imported.diff == {}
    new_status = f.UserStoryStatusFactory(project=project, name="Implemented")
    second.status = new_status
    second.save()
    take_snapshot(second, user=project.owner)
    assert "Implemented" in comments(issue).order_by("created_at").last().comment


def test_unlinked_objects_do_not_generate_bot_comments():
    for obj in [f.IssueFactory(), f.UserStoryFactory()]:
        take_snapshot(obj, user=obj.owner)
        take_snapshot(obj, user=obj.owner, comment="Standalone")
        assert comments(obj).count() == 1


def test_existing_promoted_story_without_history_mirrors_first_status_change():
    project = f.create_project()
    issue = f.IssueFactory(project=project)
    story = f.UserStoryFactory(project=project, generated_from_issue=issue)
    old_name = story.status.name
    story.status = f.UserStoryStatusFactory(project=project, name="Verified")
    story.save()
    take_snapshot(story, user=project.owner)
    mirrored = comments(issue).get()
    assert "changed the status" in mirrored.comment
    assert old_name in mirrored.comment
    assert "Verified" in mirrored.comment

import pytest
from django.urls import reverse

from taiga.base.utils import json
from taiga.projects.issues.models import Issue
from taiga.projects.userstories.models import UserStory

from .. import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def linked_issue(client):
    project = f.create_project(is_private=True, public_permissions=[], anon_permissions=[])
    f.MembershipFactory(project=project, user=project.owner, is_admin=True)
    client.login(project.owner)
    response = client.json.post(reverse("issues-list"), json.dumps({
        "project": project.pk, "subject": "Promoted issue",
    }))
    assert response.status_code == 201
    issue = Issue.objects.get(pk=response.data["id"])
    return project, issue


def listed_issue(client, issue, **params):
    response = client.json.get(reverse("issues-list"), {"project": issue.project_id, **params})
    assert response.status_code == 200
    return next(item for item in response.data if item["id"] == issue.id)


def story_for(issue, **kwargs):
    project = kwargs.pop("project", issue.project)
    return f.UserStoryFactory(project=project, generated_from_issue=issue,
                              status=project.default_us_status, milestone=None, **kwargs)


def test_unpromoted_issue_has_no_story_tags(client, linked_issue):
    _, issue = linked_issue
    assert listed_issue(client, issue)["promoted_user_stories"] == []


def test_promoted_story_status_is_returned_and_reflects_changes(client, linked_issue):
    project, issue = linked_issue
    promoted = client.json.post(reverse("issues-promote-to-user-story", args=[issue.id]),
                                json.dumps({"project_id": project.id}))
    assert promoted.status_code == 200
    story = UserStory.objects.get(generated_from_issue=issue)
    tag = listed_issue(client, issue)["promoted_user_stories"][0]
    assert tag == {"id": story.id, "ref": story.ref, "subject": story.subject,
                   "project_slug": project.slug, "status": story.status_id,
                   "status_extra_info": {"name": story.status.name, "color": story.status.color,
                                         "is_closed": story.status.is_closed}}
    status = f.UserStoryStatusFactory(project=project, name="Done", color="#218c74", is_closed=True)
    story.status = status
    story.save()
    tag = listed_issue(client, issue, status=issue.status_id)["promoted_user_stories"][0]
    assert tag["status_extra_info"] == {"name": "Done", "color": "#218c74", "is_closed": True}
    assert tag["status"] == status.id


def test_multiple_promoted_stories_are_ordered_and_unrelated_stories_are_excluded(client, linked_issue):
    project, issue = linked_issue
    one = story_for(issue)
    two = story_for(issue)
    f.UserStoryFactory(project=project, status=project.default_us_status, milestone=None)
    expected = [s.id for s in sorted([one, two], key=lambda s: (s.ref, s.id))]
    assert [s["id"] for s in listed_issue(client, issue)["promoted_user_stories"]] == expected
    one.delete()
    assert [s["id"] for s in listed_issue(client, issue)["promoted_user_stories"]] == [two.id]


@pytest.mark.parametrize("can_view_stories", [False, True])
def test_story_visibility_permissions_are_respected(client, linked_issue, can_view_stories):
    project, issue = linked_issue
    story_for(issue)
    permissions = ["view_project", "view_issues"] + (["view_us"] if can_view_stories else [])
    role = f.RoleFactory(project=project, permissions=permissions)
    user = f.UserFactory()
    f.MembershipFactory(project=project, user=user, role=role, is_admin=False)
    client.login(user)
    assert len(listed_issue(client, issue)["promoted_user_stories"]) == int(can_view_stories)


@pytest.mark.parametrize("can_view_other_project", [False, True])
def test_moved_story_uses_its_own_project_permissions_and_slug(client, linked_issue, can_view_other_project):
    project, issue = linked_issue
    other = f.create_project(is_private=True, public_permissions=[], anon_permissions=[])
    story = story_for(issue, project=other)
    if can_view_other_project:
        f.MembershipFactory(project=other, user=project.owner, is_admin=True)
    tags = listed_issue(client, issue)["promoted_user_stories"]
    if can_view_other_project:
        assert tags[0]["id"] == story.id
        assert tags[0]["project_slug"] == other.slug
    else:
        assert tags == []


def test_deleted_or_missing_status_does_not_break_issue_list(client, linked_issue):
    _, issue = linked_issue
    story = story_for(issue)
    UserStory.objects.filter(pk=story.id).update(status=None)
    tag = listed_issue(client, issue)["promoted_user_stories"][0]
    assert tag["status_extra_info"] is None


@pytest.mark.parametrize("visible", [False, True])
def test_anonymous_story_permissions_are_respected(client, linked_issue, visible):
    project, issue = linked_issue
    story_for(issue)
    project.anon_permissions = ["view_project", "view_issues"] + (["view_us"] if visible else [])
    project.save()
    client.logout()
    assert len(listed_issue(client, issue)["promoted_user_stories"]) == int(visible)


def test_list_query_count_does_not_grow_with_each_promoted_issue(client, linked_issue):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    project, issue = linked_issue
    story_for(issue)
    with CaptureQueriesContext(connection) as small:
        response = client.json.get(reverse("issues-list"), {"project": project.pk})
        assert response.status_code == 200
    for _ in range(10):
        other = f.IssueFactory(project=project, status=project.default_issue_status,
                               severity=project.default_severity, priority=project.default_priority,
                               type=project.default_issue_type, milestone=None, owner=project.owner)
        story_for(other)
    with CaptureQueriesContext(connection) as large:
        response = client.json.get(reverse("issues-list"), {"project": project.pk})
        assert response.status_code == 200
        assert len(response.data) == 11
        assert all(len(item["promoted_user_stories"]) == 1 for item in response.data)
    assert len(large) <= len(small) + 2

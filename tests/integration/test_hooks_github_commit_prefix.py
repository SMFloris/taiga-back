import pytest
from django.urls import reverse

from taiga.base.utils import json
from taiga.hooks.github.event_hooks import PushEventHook
from taiga.projects.history.services import get_history_queryset_by_model_instance, take_snapshot
from .. import factories as f


pytestmark = pytest.mark.django_db


def configured_project(client):
    project = f.create_project()
    f.MembershipFactory(project=project, user=project.owner, is_admin=True)
    client.login(project.owner)
    return project, reverse("projects-modules", kwargs={"pk": project.pk})


def test_prefix_defaults_and_partial_configuration_updates(client):
    _, url = configured_project(client)
    config = client.json.get(url).data["github"]
    assert config["commit_prefix"] == "TG"
    original_secret = config["secret"]
    response = client.json.patch(url, json.dumps({"github": {"commit_prefix": " FRISBO "}}))
    assert response.status_code == 204
    config = client.json.get(url).data["github"]
    assert config["commit_prefix"] == "FRISBO"
    assert config["secret"] == original_secret
    response = client.json.patch(url, json.dumps({"github": {"secret": "new-secret"}}))
    assert response.status_code == 204
    assert client.json.get(url).data["github"]["commit_prefix"] == "FRISBO"
    response = client.json.patch(url, json.dumps({"github": {"commit_prefix": ""}}))
    assert response.status_code == 204
    assert client.json.get(url).data["github"]["commit_prefix"] == "TG"


@pytest.mark.parametrize("prefix", [None, 12, [], "has space", "A.*", "A" * 33])
def test_invalid_prefix_is_rejected(client, prefix):
    _, url = configured_project(client)
    response = client.json.patch(url, json.dumps({"github": {"commit_prefix": prefix}}))
    assert response.status_code == 400
    assert client.json.get(url).data["github"]["commit_prefix"] == "TG"


@pytest.mark.parametrize("factory,status_factory", [
    (f.EpicFactory, f.EpicStatusFactory),
    (f.IssueFactory, f.IssueStatusFactory),
    (f.TaskFactory, f.TaskStatusFactory),
    (f.UserStoryFactory, f.UserStoryStatusFactory),
])
def test_custom_prefix_updates_status_for_each_item_type(client, factory, status_factory):
    project, url = configured_project(client)
    client.json.patch(url, json.dumps({"github": {"commit_prefix": "FRISBO"}}))
    item = factory(project=project, owner=project.owner)
    status = status_factory(project=project)
    payload = {"commits": [{"message": f"Fix frisbo-{item.ref} #{status.slug}"}]}
    PushEventHook(project, payload).process_event()
    item.refresh_from_db()
    assert item.status_id == status.id


def test_custom_prefix_mentions_ignore_old_and_embedded_prefixes(client):
    project, url = configured_project(client)
    client.json.patch(url, json.dumps({"github": {"commit_prefix": "FRISBO"}}))
    item = f.IssueFactory(project=project, owner=project.owner)
    take_snapshot(item, user=project.owner)
    for message in [f"TG-{item.ref}", f"NOTFRISBO-{item.ref}", f"OTHER-FRISBO-{item.ref}"]:
        PushEventHook(project, {"commits": [{"message": message}]}).process_event()
    assert get_history_queryset_by_model_instance(item).count() == 0
    PushEventHook(project, {"commits": [{"message": f"Fix FrIsBo-{item.ref}"}]}).process_event()
    assert get_history_queryset_by_model_instance(item).count() == 1

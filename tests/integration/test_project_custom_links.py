import pytest
from django.urls import reverse

from taiga.base.utils import json
from taiga.projects.models import ProjectTemplate
from taiga.projects.serializers import ProjectTemplateSerializer
from taiga.export_import.serializers.serializers import ProjectExportSerializer

from .. import factories as f


pytestmark = pytest.mark.django_db

LINKS = [
    {"name": "Google", "url": "https://google.com", "open_in_new_tab": False},
    {"name": "Documentation", "url": "https://example.com/docs", "open_in_new_tab": True},
]


def owned_project(client):
    project = f.create_project()
    f.MembershipFactory(project=project, user=project.owner, is_admin=True)
    client.login(project.owner)
    return project, reverse("projects-detail", kwargs={"pk": project.pk})


def test_custom_links_default_to_disabled_and_empty(client):
    project, url = owned_project(client)
    response = client.json.get(url)
    assert response.status_code == 200
    assert response.data["custom_links"] == []
    assert response.data["is_custom_links_activated"] is False


def test_project_creation_does_not_require_custom_links(client):
    owned_project(client)
    response = client.json.post(reverse("projects-list"), json.dumps({
        "name": "New project", "description": "No custom links", "is_private": True,
    }))
    assert response.status_code == 201
    assert response.data["custom_links"] == []
    assert response.data["is_custom_links_activated"] is False


def test_custom_links_round_trip_and_survive_disabling(client):
    project, url = owned_project(client)
    response = client.json.patch(url, json.dumps({
        "is_custom_links_activated": True, "custom_links": LINKS,
    }))
    assert response.status_code == 200
    assert response.data["custom_links"] == LINKS
    assert response.data["is_custom_links_activated"] is True

    response = client.json.patch(url, json.dumps({"is_custom_links_activated": False}))
    assert response.status_code == 200
    project.refresh_from_db()
    assert project.is_custom_links_activated is False
    assert project.custom_links == LINKS


@pytest.mark.parametrize("links", [
    None, {}, "https://example.com", [None],
    [{"name": "", "url": "https://example.com"}],
    [{"name": "x" * 101, "url": "https://example.com"}],
    [{"name": "Unsafe", "url": "javascript:alert(1)"}],
    [{"name": "Unsafe", "url": "data:text/html,hello"}],
    [{"name": "Relative", "url": "/path"}],
    [{"name": "Invalid", "url": "https://example.com", "open_in_new_tab": "false"}],
    LINKS * 11,
])
def test_invalid_custom_links_are_rejected(client, links):
    project, url = owned_project(client)
    response = client.json.patch(url, json.dumps({"custom_links": links}))
    assert response.status_code == 400
    project.refresh_from_db()
    assert project.custom_links == []


def test_non_admin_cannot_configure_custom_links(client):
    project, url = owned_project(client)
    member = f.MembershipFactory(project=project, is_admin=False)
    client.login(member.user)
    response = client.json.patch(url, json.dumps({"custom_links": LINKS}))
    assert response.status_code == 403


def test_custom_links_are_trimmed_and_default_to_same_tab(client):
    project, url = owned_project(client)
    response = client.json.patch(url, json.dumps({
        "custom_links": [{"name": " Google ", "url": " https://google.com "}],
    }))
    assert response.status_code == 200
    assert response.data["custom_links"] == [LINKS[0]]


def test_templates_and_exports_preserve_custom_links(client):
    project, _ = owned_project(client)
    project.is_custom_links_activated = True
    project.custom_links = LINKS
    project.save()
    template = ProjectTemplate(name="Custom links template")
    template.load_data_from_project(project)
    template.save()
    template.refresh_from_db()
    assert template.custom_links == LINKS
    assert template.is_custom_links_activated is True
    assert ProjectTemplateSerializer(template).data["custom_links"] == LINKS
    assert ProjectExportSerializer(project).data["custom_links"] == LINKS

    other = f.create_project()
    template.apply_to_project(other)
    other.save()
    other.refresh_from_db()
    assert other.custom_links == LINKS
    assert other.is_custom_links_activated is True

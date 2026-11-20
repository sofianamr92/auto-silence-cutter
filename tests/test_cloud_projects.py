from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

import pytest

from cloud.projects import ProjectConflict, SupabaseProjects


class FakeAuth:
    def current_session(self):
        return {"user_id": "owner-uuid"}

    def access_token(self):
        return "access-token"


def test_create_project_sets_authenticated_owner():
    service = SupabaseProjects(
        type("Config", (), {
            "url": "https://example.supabase.co",
            "publishable_key": "sb_publishable_test",
        })(),
        FakeAuth(),
    )
    project = {
        "id": "project-uuid",
        "name": "First edit",
        "duration_seconds": 18.5,
        "state": {"segments": []},
    }

    with patch("cloud.projects.request_json", return_value=[project]) as request:
        result = service.save_project(project)

    assert result == project
    assert request.call_args.kwargs["body"]["user_id"] == "owner-uuid"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer access-token"


def test_update_reports_newer_cloud_version_as_conflict():
    service = SupabaseProjects(
        type("Config", (), {
            "url": "https://example.supabase.co",
            "publishable_key": "sb_publishable_test",
        })(),
        FakeAuth(),
    )
    project = {
        "id": "project-uuid",
        "name": "Local edit",
        "state": {"segments": []},
    }
    remote = {"id": "project-uuid", "version": 4, "state": {"segments": [1]}}

    with patch("cloud.projects.request_json", side_effect=[[], [remote]]):
        with pytest.raises(ProjectConflict) as error:
            service.save_project(project, base_version=3)

    assert error.value.remote_project == remote


def test_list_versions_requests_only_the_authenticated_projects_history():
    service = SupabaseProjects(
        type("Config", (), {
            "url": "https://example.supabase.co",
            "publishable_key": "sb_publishable_test",
        })(),
        FakeAuth(),
    )

    with patch("cloud.projects.request_json", return_value=[]) as request:
        assert service.list_versions("project-uuid") == []

    query = parse_qs(urlparse(request.call_args.args[1]).query)
    assert query["project_id"] == ["eq.project-uuid"]
    assert query["limit"] == ["20"]
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer access-token"


def test_profile_lookup_is_scoped_to_the_authenticated_user():
    service = SupabaseProjects(
        type("Config", (), {
            "url": "https://example.supabase.co",
            "publishable_key": "sb_publishable_test",
        })(),
        FakeAuth(),
    )
    profile = {"storage_used_bytes": 1024}

    with patch("cloud.projects.request_json", return_value=[profile]) as request:
        assert service.get_profile() == profile

    query = parse_qs(urlparse(request.call_args.args[1]).query)
    assert query["id"] == ["eq.owner-uuid"]

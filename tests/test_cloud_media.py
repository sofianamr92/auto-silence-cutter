import threading
from unittest.mock import patch

from cloud.media import SupabaseMedia


class FakeAuth:
    def current_session(self):
        return {"user_id": "owner-uuid"}

    def access_token(self):
        return "access-token"


def media_service():
    config = type("Config", (), {
        "url": "https://example.supabase.co",
        "publishable_key": "sb_publishable_test",
    })()
    return SupabaseMedia(config, FakeAuth())


def test_storage_requests_use_supabase_direct_storage_hostname():
    service = media_service()

    assert service._storage_url() == "https://example.storage.supabase.co"


def test_start_upload_creates_owner_scoped_asset_and_resumable_state(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"sample video")
    service = media_service()

    with (
        patch("cloud.media.request_json", side_effect=[
            [{"id": "project-uuid", "media_status": "local_only"}],
            None,
        ]) as request,
        patch.object(service, "_create_tus_resource", return_value="https://upload.test/tus/1"),
        patch.object(service, "_start_worker"),
    ):
        upload = service.start_upload(
            "project-uuid",
            str(source),
            "original.mp4",
            "video/mp4",
        )

    asset = request.call_args_list[1].kwargs["body"]
    assert asset["user_id"] == "owner-uuid"
    assert asset["project_id"] == "project-uuid"
    assert asset["storage_path"].startswith("owner-uuid/project-uuid/")
    assert upload["status"] == "uploading"
    assert upload["total_bytes"] == len(b"sample video")
    assert service.status(upload["upload_id"], "another-user") is None


def test_start_upload_refuses_projects_not_visible_to_the_user(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"sample video")
    service = media_service()

    with (
        patch("cloud.media.request_json", return_value=[]) as request,
        patch.object(service, "_create_tus_resource") as create_upload,
    ):
        try:
            service.start_upload(
                "other-project",
                str(source),
                "original.mp4",
                "video/mp4",
            )
        except ValueError as error:
            assert "غير موجود" in str(error)
        else:
            raise AssertionError("An inaccessible project must not accept media uploads.")

    request.assert_called_once()
    create_upload.assert_not_called()


def test_upload_worker_verifies_and_marks_media_complete(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"sample video")
    service = media_service()
    state = {
        "upload_id": "asset-uuid",
        "asset_id": "asset-uuid",
        "project_id": "project-uuid",
        "user_id": "owner-uuid",
        "previous_media_status": "local_only",
        "source_path": str(source),
        "object_path": "owner-uuid/project-uuid/asset-uuid.mp4",
        "location": "https://upload.test/tus/1",
        "total_bytes": source.stat().st_size,
        "uploaded_bytes": 0,
        "progress": 0,
        "status": "uploading",
        "message": "",
        "pause_event": threading.Event(),
        "cancel_event": threading.Event(),
        "thread": None,
    }

    with (
        patch.object(service, "_hash_file", return_value="a" * 64),
        patch.object(service, "_tus_head", return_value=0),
        patch.object(service, "_tus_patch", return_value=source.stat().st_size),
        patch.object(service, "_verify_remote_object") as verify,
        patch.object(service, "_update_asset") as update_asset,
        patch.object(service, "_update_project_media_status", return_value=[{"version": 3}]),
    ):
        service._upload_worker(state)

    verify.assert_called_once_with(state, "a" * 64)
    update_asset.assert_called_once_with(
        "asset-uuid",
        "owner-uuid",
        {"upload_status": "complete", "sha256": "a" * 64},
    )
    assert state["status"] == "completed"
    assert state["project_version"] == 3

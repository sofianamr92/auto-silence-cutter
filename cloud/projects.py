import json
from urllib.parse import urlencode

from cloud.http import CloudRequestError, request_json


class ProjectConflict(RuntimeError):
    def __init__(self, remote_project):
        super().__init__("A newer cloud version of this project already exists.")
        self.remote_project = remote_project


class SupabaseProjects:
    def __init__(self, config, auth):
        self.config = config
        self.auth = auth

    def _headers(self, prefer=None):
        headers = {
            "apikey": self.config.publishable_key,
            "Authorization": f"Bearer {self.auth.access_token()}",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def list_projects(self):
        query = urlencode({
            "select": "id,name,source_filename,duration_seconds,media_size_bytes,thumbnail_data,version,media_status,created_at,updated_at,last_cloud_saved_at",
            "order": "updated_at.desc",
        })
        return request_json(
            "GET",
            f"{self.config.url}/rest/v1/projects?{query}",
            headers=self._headers(),
        )

    def get_profile(self):
        session = self.auth.current_session()
        if not session:
            raise CloudRequestError("Sign in before loading your account profile.")
        query = urlencode({
            "id": f"eq.{session['user_id']}",
            "select": "display_name,email,avatar_url,created_at,last_sign_in_at,storage_used_bytes",
        })
        result = request_json(
            "GET",
            f"{self.config.url}/rest/v1/profiles?{query}",
            headers=self._headers(),
        )
        return result[0] if result else None

    def get_project(self, project_id):
        query = urlencode({"id": f"eq.{project_id}", "select": "*"})
        result = request_json(
            "GET",
            f"{self.config.url}/rest/v1/projects?{query}",
            headers=self._headers(),
        )
        return result[0] if result else None

    def list_versions(self, project_id):
        query = urlencode({
            "project_id": f"eq.{project_id}",
            "select": "id,version,state,created_at",
            "order": "version.desc",
            "limit": "20",
        })
        return request_json(
            "GET",
            f"{self.config.url}/rest/v1/project_versions?{query}",
            headers=self._headers(),
        )

    def save_project(self, project, base_version=None):
        project_id = project.get("id")
        if not project_id:
            raise ValueError("A project UUID is required.")

        if base_version is None:
            session = self.auth.current_session()
            if not session:
                raise CloudRequestError("Sign in before creating a cloud project.")
            payload = {
                "id": project_id,
                "user_id": session["user_id"],
                "name": project["name"],
                "source_filename": project.get("source_filename"),
                "duration_seconds": project.get("duration_seconds"),
                "media_size_bytes": project.get("media_size_bytes", 0),
                "thumbnail_data": project.get("thumbnail_data"),
                "state": project["state"],
                "media_status": project.get("media_status", "local_only"),
            }
            result = request_json(
                "POST",
                f"{self.config.url}/rest/v1/projects",
                headers=self._headers("return=representation"),
                body=payload,
            )
            return result[0] if isinstance(result, list) and result else result

        query = urlencode({
            "id": f"eq.{project_id}",
            "version": f"eq.{int(base_version)}",
        })
        payload = {
            "name": project["name"],
            "source_filename": project.get("source_filename"),
            "duration_seconds": project.get("duration_seconds"),
            "media_size_bytes": project.get("media_size_bytes", 0),
            "thumbnail_data": project.get("thumbnail_data"),
            "state": project["state"],
            "media_status": project.get("media_status", "local_only"),
        }
        result = request_json(
            "PATCH",
            f"{self.config.url}/rest/v1/projects?{query}",
            headers=self._headers("return=representation"),
            body=payload,
        )
        if isinstance(result, list) and result:
            return result[0]
        remote = self.get_project(project_id)
        if remote:
            raise ProjectConflict(remote)
        raise CloudRequestError("The cloud project was deleted or is not accessible.")

    def delete_project(self, project_id):
        query = urlencode({"id": f"eq.{project_id}"})
        request_json(
            "DELETE",
            f"{self.config.url}/rest/v1/projects?{query}",
            headers=self._headers("return=minimal"),
        )

    def duplicate_project(self, project):
        duplicate = {
            "id": project["id"],
            "name": f"{project['name']} (copy)",
            "source_filename": project.get("source_filename"),
            "duration_seconds": project.get("duration_seconds"),
            "state": json.loads(json.dumps(project["state"])),
            "media_status": project.get("media_status", "local_only"),
        }
        return self.save_project(duplicate)

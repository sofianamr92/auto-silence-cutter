import base64
import hashlib
import os
import re
import threading
import time
import uuid
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cloud.http import CloudRequestError, request_json


class MediaUploadError(RuntimeError):
    pass


class SupabaseMedia:
    BUCKET = "project-media"
    CHUNK_SIZE = 6 * 1024 * 1024

    def __init__(self, config, auth):
        self.config = config
        self.auth = auth
        self._uploads = {}
        self._lock = threading.RLock()

    def _storage_url(self):
        parsed = urlparse(self.config.url)
        suffix = ".supabase.co"
        if parsed.hostname and parsed.hostname.endswith(suffix):
            project_ref = parsed.hostname[:-len(suffix)]
            if project_ref and "." not in project_ref:
                return f"{parsed.scheme}://{project_ref}.storage.supabase.co"
        return self.config.url

    def _headers(self, prefer=None):
        headers = {
            "apikey": self.config.publishable_key,
            "Authorization": f"Bearer {self.auth.access_token()}",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _update_asset(self, asset_id, user_id, fields):
        query = urlencode({
            "id": f"eq.{asset_id}",
            "user_id": f"eq.{user_id}",
        })
        request_json(
            "PATCH",
            f"{self.config.url}/rest/v1/project_assets?{query}",
            headers=self._headers("return=minimal"),
            body=fields,
        )

    def _update_project_media_status(self, project_id, user_id, status, size_bytes=None):
        query = urlencode({
            "id": f"eq.{project_id}",
            "user_id": f"eq.{user_id}",
        })
        fields = {"media_status": status}
        if size_bytes is not None:
            fields["media_size_bytes"] = size_bytes
        return request_json(
            "PATCH",
            f"{self.config.url}/rest/v1/projects?{query}",
            headers=self._headers("return=representation"),
            body={"media_status": status},
        )

    @staticmethod
    def _metadata_value(value):
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    def _create_tus_resource(self, object_path, size, content_type):
        metadata = {
            "bucketName": self.BUCKET,
            "objectName": object_path,
            "contentType": content_type,
            "cacheControl": "3600",
        }
        encoded_metadata = ",".join(
            f"{key} {self._metadata_value(value)}" for key, value in metadata.items()
        )
        request = Request(
            f"{self._storage_url()}/storage/v1/upload/resumable",
            data=b"",
            headers={
                **self._headers(),
                "Tus-Resumable": "1.0.0",
                "Upload-Length": str(size),
                "Upload-Metadata": encoded_metadata,
                "x-upsert": "false",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                location = response.headers.get("Location")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise MediaUploadError("Could not start the resumable Supabase upload.") from exc
        if not location:
            raise MediaUploadError("Supabase did not return a resumable upload URL.")
        return urljoin(f"{self._storage_url()}/", location)

    def start_upload(self, project_id, source_path, original_filename, content_type):
        if not os.path.isfile(source_path):
            raise ValueError("ملف الفيديو غير موجود على هذا الجهاز.")
        size = os.path.getsize(source_path)
        if size <= 0:
            raise ValueError("ملف الفيديو فارغ.")
        if not content_type or not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", content_type):
            content_type = "application/octet-stream"

        session = self.auth.current_session()
        if not session:
            raise CloudRequestError("Sign in before uploading media.")
        user_id = session["user_id"]
        safe_name = os.path.basename(original_filename or os.path.basename(source_path))
        extension = os.path.splitext(safe_name)[1].lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", extension):
            extension = ".bin"
        asset_id = str(uuid.uuid4())
        object_path = f"{user_id}/{project_id}/{asset_id}{extension}"

        query = urlencode({"id": f"eq.{project_id}", "select": "id,media_status"})
        project = request_json(
            "GET",
            f"{self.config.url}/rest/v1/projects?{query}",
            headers=self._headers(),
        )
        if not project:
            raise ValueError("المشروع غير موجود أو لا يمكن الوصول إليه.")
        previous_media_status = project[0].get("media_status") or "local_only"

        request_json(
            "POST",
            f"{self.config.url}/rest/v1/project_assets",
            headers=self._headers("return=minimal"),
            body={
                "id": asset_id,
                "project_id": project_id,
                "user_id": user_id,
                "asset_type": "video",
                "storage_path": object_path,
                "original_filename": safe_name,
                "content_type": content_type,
                "file_size_bytes": size,
                "upload_status": "uploading",
            },
        )
        try:
            location = self._create_tus_resource(object_path, size, content_type)
        except Exception:
            self._update_asset(asset_id, user_id, {"upload_status": "failed"})
            raise

        pause_event = threading.Event()
        cancel_event = threading.Event()
        state = {
            "upload_id": asset_id,
            "asset_id": asset_id,
            "project_id": project_id,
            "user_id": user_id,
            "previous_media_status": previous_media_status,
            "source_path": os.path.abspath(source_path),
            "original_filename": safe_name,
            "object_path": object_path,
            "location": location,
            "total_bytes": size,
            "uploaded_bytes": 0,
            "progress": 0,
            "status": "uploading",
            "message": "جارٍ رفع الفيديو إلى التخزين الخاص…",
            "pause_event": pause_event,
            "cancel_event": cancel_event,
            "thread": None,
        }
        with self._lock:
            self._uploads[asset_id] = state
        self._start_worker(state)
        return self.status(asset_id, user_id)

    def _start_worker(self, state):
        thread = threading.Thread(target=self._upload_worker, args=(state,), daemon=True)
        state["thread"] = thread
        thread.start()

    def _tus_head(self, location):
        request = Request(
            location,
            headers={**self._headers(), "Tus-Resumable": "1.0.0"},
            method="HEAD",
        )
        with urlopen(request, timeout=30) as response:
            value = response.headers.get("Upload-Offset")
        if value is None:
            raise MediaUploadError("Supabase did not return the resumable upload offset.")
        return int(value)

    def _tus_patch(self, location, offset, data):
        request = Request(
            location,
            data=data,
            headers={
                **self._headers(),
                "Tus-Resumable": "1.0.0",
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
            },
            method="PATCH",
        )
        with urlopen(request, timeout=120) as response:
            value = response.headers.get("Upload-Offset")
        if value is None:
            raise MediaUploadError("Supabase did not confirm the uploaded chunk offset.")
        return int(value)

    def _hash_file(self, path):
        digest = hashlib.sha256()
        with open(path, "rb") as media_file:
            while True:
                chunk = media_file.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _verify_remote_object(self, state, expected_sha256):
        object_url = (
            f"{self._storage_url()}/storage/v1/object/authenticated/"
            f"{self.BUCKET}/{quote(state['object_path'], safe='/')}"
        )
        request = Request(object_url, headers=self._headers(), method="GET")
        digest = hashlib.sha256()
        try:
            with urlopen(request, timeout=120) as response:
                while True:
                    chunk = response.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise MediaUploadError("Could not read the uploaded video back for checksum verification.") from exc
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise MediaUploadError("The uploaded video checksum does not match the local file.")

    def _upload_worker(self, state):
        upload_id = state["upload_id"]
        try:
            expected_hash = self._hash_file(state["source_path"])
            offset = self._tus_head(state["location"])
            attempts = 0
            with open(state["source_path"], "rb") as media_file:
                media_file.seek(offset)
                while offset < state["total_bytes"]:
                    if state["cancel_event"].is_set():
                        state["status"] = "cancelled"
                        state["message"] = "تم إيقاف رفع الفيديو."
                        self._update_asset(state["asset_id"], state["user_id"], {"upload_status": "cancelled"})
                        return
                    if state["pause_event"].is_set():
                        state["status"] = "paused"
                        state["message"] = "تم إيقاف الرفع مؤقتاً."
                        while state["pause_event"].is_set() and not state["cancel_event"].wait(0.25):
                            pass
                        if state["cancel_event"].is_set():
                            continue
                        state["status"] = "uploading"
                        state["message"] = "استؤنف رفع الفيديو…"

                    media_file.seek(offset)
                    chunk = media_file.read(min(self.CHUNK_SIZE, state["total_bytes"] - offset))
                    try:
                        new_offset = self._tus_patch(state["location"], offset, chunk)
                        if new_offset != offset + len(chunk):
                            raise MediaUploadError("Supabase confirmed an unexpected upload offset.")
                        offset = new_offset
                        attempts = 0
                    except HTTPError as exc:
                        if exc.code not in (408, 409, 425, 429) and exc.code < 500:
                            raise MediaUploadError(
                                f"Supabase rejected the upload chunk (HTTP {exc.code})."
                            ) from exc
                        attempts += 1
                        if attempts > 8:
                            raise MediaUploadError("The upload paused after repeated network failures.") from exc
                        time.sleep(min(2 ** min(attempts, 5), 30))
                        offset = self._tus_head(state["location"])
                    except (URLError, TimeoutError):
                        attempts += 1
                        if attempts > 8:
                            raise MediaUploadError("The upload paused after repeated network failures.")
                        time.sleep(min(2 ** min(attempts, 5), 30))
                        offset = self._tus_head(state["location"])
                    state["uploaded_bytes"] = offset
                    state["progress"] = round(offset * 100 / state["total_bytes"])

            state["message"] = "جارٍ التحقق من سلامة الفيديو المرفوع…"
            self._verify_remote_object(state, expected_hash)
            self._update_asset(
                state["asset_id"],
                state["user_id"],
                {
                    "upload_status": "complete",
                    "sha256": expected_hash,
                },
            )
            result = self._update_project_media_status(
                state["project_id"],
                state["user_id"],
                "cloud",
                state["total_bytes"],
            )
            state["project_version"] = result[0]["version"] if isinstance(result, list) and result else None
            state["sha256"] = expected_hash
            state["status"] = "completed"
            state["progress"] = 100
            state["message"] = "تم رفع الفيديو والتحقق من checksum."
        except Exception as exc:
            state["status"] = "error"
            state["message"] = str(exc)
            try:
                self._update_asset(
                    state["asset_id"],
                    state["user_id"],
                    {"upload_status": "failed"},
                )
            except CloudRequestError:
                pass

    def status(self, upload_id, user_id):
        with self._lock:
            state = self._uploads.get(upload_id)
            if not state or state["user_id"] != user_id:
                return None
            return {
                "upload_id": state["upload_id"],
                "project_id": state["project_id"],
                "previous_media_status": state["previous_media_status"],
                "total_bytes": state["total_bytes"],
                "uploaded_bytes": state["uploaded_bytes"],
                "progress": state["progress"],
                "status": state["status"],
                "message": state["message"],
                "project_version": state.get("project_version"),
                "sha256": state.get("sha256"),
            }

    def control(self, upload_id, user_id, action):
        with self._lock:
            state = self._uploads.get(upload_id)
            if not state or state["user_id"] != user_id:
                raise ValueError("عملية الرفع غير موجودة.")
            if action == "pause":
                state["pause_event"].set()
            elif action == "resume":
                state["pause_event"].clear()
                thread = state.get("thread")
                if not thread or not thread.is_alive():
                    state["status"] = "uploading"
                    self._start_worker(state)
            elif action == "cancel":
                state["cancel_event"].set()
                state["pause_event"].clear()
            else:
                raise ValueError("أمر الرفع غير صالح.")
            return self.status(upload_id, user_id)

    def download_project_video(self, project_id):
        session = self.auth.current_session()
        if not session:
            raise CloudRequestError("Sign in before downloading cloud media.")
        query = urlencode({
            "project_id": f"eq.{project_id}",
            "user_id": f"eq.{session['user_id']}",
            "asset_type": "eq.video",
            "upload_status": "eq.complete",
            "select": "storage_path,original_filename,file_size_bytes,sha256",
            "order": "created_at.desc",
        })
        assets = request_json(
            "GET",
            f"{self.config.url}/rest/v1/project_assets?{query}",
            headers=self._headers(),
        )
        if not assets:
            return None
        asset = assets[0]
        filename = os.path.basename(asset.get("original_filename") or "project-video.mp4")
        local_root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        destination_dir = os.path.join(
            local_root,
            "AutoSilenceCutter",
            "media",
            session["user_id"],
            project_id,
        )
        os.makedirs(destination_dir, exist_ok=True)
        destination = os.path.join(destination_dir, filename)
        temporary_path = f"{destination}.part"
        object_url = (
            f"{self._storage_url()}/storage/v1/object/authenticated/"
            f"{self.BUCKET}/{quote(asset['storage_path'], safe='/')}"
        )
        request = Request(object_url, headers=self._headers(), method="GET")
        digest = hashlib.sha256()
        received = 0
        try:
            with urlopen(request, timeout=120) as response, open(temporary_path, "wb") as target:
                while True:
                    chunk = response.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    target.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
            raise MediaUploadError("تعذر تنزيل ملف الفيديو من التخزين السحابي.") from exc
        if received != int(asset["file_size_bytes"]) or digest.hexdigest() != asset["sha256"]:
            os.remove(temporary_path)
            raise MediaUploadError("فشل التحقق من سلامة الفيديو بعد التنزيل.")
        os.replace(temporary_path, destination)
        return {
            "path": destination,
            "filename": filename,
            "size_bytes": received,
            "sha256": digest.hexdigest(),
        }

    def delete_project_assets(self, project_id):
        session = self.auth.current_session()
        if not session:
            raise CloudRequestError("Sign in before deleting project media.")
        query = urlencode({
            "project_id": f"eq.{project_id}",
            "user_id": f"eq.{session['user_id']}",
            "select": "storage_path",
        })
        assets = request_json(
            "GET",
            f"{self.config.url}/rest/v1/project_assets?{query}",
            headers=self._headers(),
        )
        paths = [asset["storage_path"] for asset in assets or []]
        if paths:
            request_json(
                "DELETE",
                f"{self._storage_url()}/storage/v1/object/{self.BUCKET}",
                headers=self._headers(),
                body={"prefixes": paths},
            )

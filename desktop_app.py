"""
desktop_app.py
المحرك المكتبي مع دعم التصدير فائق السرعة وتحديث نسبة التقدم لحظياً
"""
import os
import sys
import json
import csv
import urllib.parse
import subprocess
import webbrowser
import tkinter as tk
from tkinter import filedialog
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import queue
import threading
import tempfile
import logging
import math
import shutil
import html
import uuid
import time
from core.editing_engine import FFmpegRenderer, merge_contiguous_segments
from core.media_http import parse_byte_range, video_content_type
from core.media_probe import MediaProbe
from core.timecode import format_timecode
from export.capcut_package import CapCutPackageExporter
from export.export_settings import (
    ExportSettings,
    ExportSettingsError,
    detect_ffmpeg_capabilities,
    select_segments_for_range,
)

from cloud.auth import CloudAuthError, SupabaseAuth
from cloud.config import CloudConfig, CloudConfigurationError
from cloud.http import CloudRequestError
from cloud.media import MediaUploadError, SupabaseMedia
from cloud.projects import ProjectConflict, SupabaseProjects

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = 8990

# ملفات الواجهة المسموح بتقديمها فقط (أي حاجة تانية = 404)
STATIC_EXTENSIONS = {
    ".html", ".js", ".mjs", ".css", ".json", ".map", ".wasm",
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".mp3", ".wav",
}
# فولدرات ما تتقدمش للمتصفح أبداً
BLOCKED_DIRS = {
    "cloud", "core", "tests", "supabase", "database",
    "scripts", "projects", "workspace", "shortcuts",
}

root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)

# نوافذ tkinter لازم تتفتح من نفس الـ thread اللي اتعمل فيه root (الـ main thread).
# handlers السيرفر بتبعت الطلب هنا وتستنى النتيجة.
DIALOG_QUEUE = queue.Queue()


def run_dialog_on_main(func, *args, **kwargs):
    done = threading.Event()
    box = {}
    DIALOG_QUEUE.put((func, args, kwargs, box, done))
    if not done.wait(timeout=600):
        raise TimeoutError("انتهت مهلة نافذة الاختيار.")
    if "error" in box:
        raise box["error"]
    return box.get("result")

CURRENT_ACTIVE_VIDEO = None
UPLOADED_SOURCE_PATH = None
logger = logging.getLogger(__name__)

try:
    CLOUD_CONFIG = CloudConfig.from_environment(BASE_DIR)
    CLOUD_CONFIG_ERROR = None
except CloudConfigurationError as exc:
    CLOUD_CONFIG = None
    CLOUD_CONFIG_ERROR = str(exc)
    logger.error("Cloud configuration is invalid: %s", exc)

CLOUD_AUTH = SupabaseAuth(CLOUD_CONFIG)
CLOUD_PROJECTS = SupabaseProjects(CLOUD_CONFIG, CLOUD_AUTH) if CLOUD_CONFIG else None
CLOUD_MEDIA = SupabaseMedia(CLOUD_CONFIG, CLOUD_AUTH) if CLOUD_CONFIG else None

# حالة عملية التصدير الحالية
EXPORT_STATE = {
    "status": "idle",       # idle | exporting | completed | error
    "progress": 0,          # 0 to 100
    "message": "",
    "output_path": "",
    "stage": "idle",
    "elapsed_seconds": 0,
    "estimated_remaining_seconds": None,
    "fps": None,
    "speed": None,
    "output_size_bytes": None,
}
SOURCE_EXPORT_LOCK = threading.RLock()
EXPORT_CANCEL_EVENT = None


def remove_uploaded_source(path):
    if not path or not os.path.isfile(path):
        return
    try:
        os.remove(path)
    except OSError:
        logger.warning("Could not remove temporary source file %s", path, exc_info=True)


def validate_export_segments(segments):
    if not isinstance(segments, list) or not segments:
        raise ValueError("لا توجد مقاطع صالحة للتصدير.")
    validated = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("بيانات أحد المقاطع غير صالحة.")
        start = float(segment["start"])
        end = float(segment["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("توقيت أحد المقاطع غير صالح.")
        item = {"start": start, "end": end}
        clip_id = segment.get("id") or segment.get("clip_id")
        if isinstance(clip_id, str):
            item["id"] = clip_id
        validated.append(item)
    return merge_contiguous_segments(validated)


def apply_export_range(segments, settings):
    return select_segments_for_range(segments, settings)


def _commit_text_files(contents, overwrite):
    temp_paths = []
    created_paths = []
    try:
        for path, text in contents.items():
            directory = os.path.dirname(os.path.abspath(path))
            os.makedirs(directory, exist_ok=True)
            descriptor, temp_path = tempfile.mkstemp(
                prefix=f".{os.path.basename(path)}.",
                suffix=".tmp",
                dir=directory,
                text=True,
            )
            temp_paths.append(temp_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        if overwrite:
            for (path, _text), temp_path in zip(contents.items(), temp_paths):
                os.replace(temp_path, path)
            return
        for (path, _text), temp_path in zip(contents.items(), temp_paths):
            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise FileExistsError(f"يوجد ملف بهذا الاسم بالفعل: {path}") from exc
            except OSError:
                # hard links غير مدعومة (exFAT / FAT32 / بعض الأقراص الشبكية)
                if os.path.exists(path):
                    raise FileExistsError(f"يوجد ملف بهذا الاسم بالفعل: {path}")
                os.rename(temp_path, path)
                created_paths.append(path)
                continue
            created_paths.append(path)
            os.remove(temp_path)
    except Exception:
        for path in created_paths:
            try:
                os.remove(path)
            except OSError:
                logger.warning("Could not remove partial export metadata %s", path, exc_info=True)
        raise
    finally:
        for path in temp_paths:
            if os.path.exists(path):
                os.remove(path)


class CustomHandler(SimpleHTTPRequestHandler):
    
    def translate_path(self, path):
        clean_path = urllib.parse.unquote(path.split('?')[0].split('#')[0])
        parts = [p for p in clean_path.replace('\\', '/').split('/') if p]
        if not parts:
            return os.path.join(BASE_DIR, 'AutoSilenceCutter.html')
        blocked = os.path.join(BASE_DIR, '__blocked__')
        try:
            base = os.path.realpath(BASE_DIR)
            full = os.path.realpath(os.path.join(BASE_DIR, *parts))
            inside = os.path.commonpath([base, full]) == base
        except ValueError:
            return blocked
        if (
            not inside
            or any(p.startswith('.') for p in parts)
            or parts[0].lower() in BLOCKED_DIRS
            or os.path.splitext(full)[1].lower() not in STATIC_EXTENSIONS
        ):
            return blocked
        return full

    def do_GET(self):
        request = urllib.parse.urlparse(self.path)
        if request.path.startswith('/api/') and not self._check_local_origin():
            return
        if request.path == '/auth/callback':
            self._handle_google_callback(request)
            return

        if request.path == '/api/cloud/status':
            self._send_json({
                "configured": CLOUD_CONFIG is not None,
                "configuration_error": CLOUD_CONFIG_ERROR,
            })
            return

        if request.path == '/api/auth/session':
            try:
                session = CLOUD_AUTH.current_session()
                self._send_json({
                    "configured": CLOUD_CONFIG is not None,
                    "user": session.get("user") if session else None,
                })
            except CloudAuthError as exc:
                self._send_json({
                    "configured": CLOUD_CONFIG is not None,
                    "user": None,
                    "error": str(exc),
                }, status=503 if CLOUD_CONFIG is None else 401)
            return

        if request.path == '/api/cloud/projects':
            if not self._check_local_origin():
                return
            try:
                self._require_cloud_user()
                self._send_json({"success": True, "projects": CLOUD_PROJECTS.list_projects()})
            except (CloudAuthError, CloudRequestError) as exc:
                self._send_cloud_error(exc)
            return

        if request.path == '/api/account/profile':
            if not self._check_local_origin():
                return
            try:
                self._require_cloud_user()
                profile = CLOUD_PROJECTS.get_profile()
                if profile is None:
                    raise CloudRequestError("تعذر العثور على ملف الحساب.")
                self._send_json({"success": True, "profile": profile})
            except (CloudAuthError, CloudRequestError) as exc:
                self._send_cloud_error(exc)
            return

        if request.path == '/api/cloud/projects/get':
            if not self._check_local_origin():
                return
            try:
                self._require_cloud_user()
                project_id = urllib.parse.parse_qs(request.query).get('project_id', [None])[0]
                if not project_id:
                    raise ValueError("معرف المشروع غير محدد.")
                project = CLOUD_PROJECTS.get_project(project_id)
                if not project:
                    self._send_json({"success": False, "error": "المشروع غير موجود."}, status=404)
                else:
                    self._send_json({"success": True, "project": project})
            except (CloudAuthError, CloudRequestError, ValueError) as exc:
                self._send_cloud_error(exc)
            return

        if request.path == '/api/cloud/projects/versions':
            if not self._check_local_origin():
                return
            try:
                self._require_cloud_user()
                project_id = urllib.parse.parse_qs(request.query).get('project_id', [None])[0]
                if not project_id:
                    raise ValueError("معرف المشروع غير محدد.")
                self._send_json({
                    "success": True,
                    "versions": CLOUD_PROJECTS.list_versions(project_id),
                })
            except (CloudAuthError, CloudRequestError, ValueError) as exc:
                self._send_cloud_error(exc)
            return

        if request.path == '/api/cloud/media/status':
            if not self._check_local_origin():
                return
            try:
                session = self._require_cloud_user()
                upload_id = urllib.parse.parse_qs(request.query).get('upload_id', [None])[0]
                result = CLOUD_MEDIA.status(upload_id, session["user_id"]) if upload_id else None
                if result is None:
                    self._send_json({"success": False, "error": "عملية الرفع غير موجودة."}, status=404)
                else:
                    self._send_json({"success": True, "upload": result})
            except (CloudAuthError, CloudRequestError) as exc:
                self._send_cloud_error(exc)
            return

        if request.path == '/api/cloud/media/download':
            if not self._check_local_origin():
                return
            try:
                self._require_cloud_user()
                project_id = urllib.parse.parse_qs(request.query).get('project_id', [None])[0]
                if not project_id:
                    raise ValueError("معرف المشروع غير محدد.")
                result = CLOUD_MEDIA.download_project_video(project_id)
                if result is None:
                    self._send_json({"success": False, "error": "لا يوجد فيديو سحابي لهذا المشروع."}, status=404)
                else:
                    self._send_json({"success": True, "media": result})
            except (CloudAuthError, CloudRequestError, MediaUploadError, ValueError) as exc:
                self._send_cloud_error(exc)
            return

        if request.path == '/api/export_capabilities':
            try:
                self._send_json({
                    "success": True,
                    "capabilities": detect_ffmpeg_capabilities(),
                })
            except RuntimeError as exc:
                logger.exception("Could not inspect FFmpeg export capabilities")
                self._send_json({"success": False, "error": str(exc)}, status=503)
            return

        # 1. الاستعلام عن نسبة تقدم التصدير
        if request.path == '/api/export_progress':
            with SOURCE_EXPORT_LOCK:
                state = dict(EXPORT_STATE)
            self._send_json(state)
            return

        # 2. مشغل بث الفيديو
        if request.path == '/api/stream_video':
            requested = urllib.parse.parse_qs(request.query).get('path', [None])[0]
            if (
                not requested
                or not CURRENT_ACTIVE_VIDEO
                or not os.path.isfile(CURRENT_ACTIVE_VIDEO)
                or os.path.normcase(os.path.abspath(requested))
                != os.path.normcase(CURRENT_ACTIVE_VIDEO)
            ):
                self.send_error(404, "الفيديو غير موجود")
                return
            self._stream_file(CURRENT_ACTIVE_VIDEO)
            return

        super().do_GET()

    def _handle_google_callback(self, request):
        if not self._check_local_origin():
            return
        params = urllib.parse.parse_qs(request.query)
        if params.get('error'):
            message = params.get('error_description', params['error'])[0]
            self._send_callback_page(False, message)
            return

        code = params.get('code', [None])[0]
        if not code:
            self._send_callback_page(False, "لم يصل رمز تسجيل الدخول من Google.")
            return
        try:
            CLOUD_AUTH.finish_google_login(code)
        except CloudAuthError as exc:
            logger.warning("Google sign-in callback failed: %s", exc)
            self._send_callback_page(False, str(exc))
            return
        self._send_callback_page(True, "تم تسجيل الدخول بنجاح. يمكنك العودة إلى التطبيق.")

    def _send_callback_page(self, success, message):
        safe_message = html.escape(message)
        status_text = "نجاح" if success else "تعذر تسجيل الدخول"
        body = (
            "<!doctype html><html lang='ar' dir='rtl'><meta charset='utf-8'>"
            f"<title>{status_text}</title><body style='font-family:Segoe UI,sans-serif;"
            f"background:#080c14;color:#e5e7eb;padding:3rem;text-align:center'>"
            f"<h1>{status_text}</h1><p>{safe_message}</p>"
            "<p>ارجع إلى نافذة Auto Silence Cutter لإكمال العمل.</p></body></html>"
        ).encode("utf-8")
        self.send_response(200 if success else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _check_local_origin(self):
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        origin = self.headers.get("Origin")
        allowed_origins = {
            f"http://127.0.0.1:{PORT}",
            f"http://localhost:{PORT}",
        }
        if (
            self.client_address[0] not in ("127.0.0.1", "::1")
            or host not in ("127.0.0.1", "localhost")
            or (origin and origin not in allowed_origins)
        ):
            self._send_json({"success": False, "error": "طلب محلي غير موثوق."}, status=403)
            return False
        return True

    def _require_cloud_user(self):
        if not CLOUD_CONFIG:
            raise CloudAuthError("أضف إعدادات Supabase إلى ملف .env أولاً.")
        session = CLOUD_AUTH.current_session()
        if not session:
            raise CloudAuthError("سجّل الدخول باستخدام Google أولاً.")
        return session

    def _send_cloud_error(self, exc):
        if isinstance(exc, ProjectConflict):
            self._send_json({
                "success": False,
                "conflict": True,
                "error": str(exc),
                "remote_project": exc.remote_project,
            }, status=409)
            return
        if isinstance(exc, CloudAuthError):
            status = 503 if not CLOUD_CONFIG else 401
        elif isinstance(exc, CloudRequestError):
            status = exc.status if exc.status and 400 <= exc.status < 500 else 502
        elif isinstance(exc, ValueError):
            status = 400
        else:
            logger.exception("Cloud operation failed")
            status = 500
        self._send_json({"success": False, "error": str(exc)}, status=status)

    def _handle_source_upload(self, content_length):
        global CURRENT_ACTIVE_VIDEO, UPLOADED_SOURCE_PATH, EXPORT_STATE
        with SOURCE_EXPORT_LOCK:
            if EXPORT_STATE["status"] == "exporting":
                self._send_json({"success": False, "error": "انتظر انتهاء التصدير قبل تحميل مصدر آخر."}, status=409)
                return
        if content_length <= 0:
            self._send_json({"success": False, "error": "ملف المصدر فارغ."}, status=400)
            return

        filename = urllib.parse.unquote(self.headers.get("X-Filename", ""))
        suffix = os.path.splitext(os.path.basename(filename))[1].lower()
        if not suffix or len(suffix) > 10:
            suffix = ".mp4"

        temp_path = None
        try:
            descriptor, temp_path = tempfile.mkstemp(prefix="auto_silence_source_", suffix=suffix)
            remaining = content_length
            with os.fdopen(descriptor, "wb") as source_file:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ConnectionError("انقطع رفع ملف المصدر قبل اكتماله.")
                    source_file.write(chunk)
                    remaining -= len(chunk)

            if os.path.getsize(temp_path) == 0:
                raise ValueError("ملف المصدر فارغ.")

            with SOURCE_EXPORT_LOCK:
                if EXPORT_STATE["status"] == "exporting":
                    remove_uploaded_source(temp_path)
                    temp_path = None
                    self._send_json({"success": False, "error": "انتظر انتهاء التصدير قبل تحميل مصدر آخر."}, status=409)
                    return
                previous_upload = UPLOADED_SOURCE_PATH
                CURRENT_ACTIVE_VIDEO = os.path.abspath(temp_path)
                UPLOADED_SOURCE_PATH = CURRENT_ACTIVE_VIDEO
                EXPORT_STATE = {
                    "status": "idle",
                    "progress": 0,
                    "message": "",
                    "output_path": ""
                }
                temp_path = None
                remove_uploaded_source(previous_upload)
            self._send_json({
                "success": True,
                "path": CURRENT_ACTIVE_VIDEO,
                "size_bytes": os.path.getsize(CURRENT_ACTIVE_VIDEO),
            })
        except Exception as exc:
            logger.exception("Source upload failed")
            remove_uploaded_source(temp_path)
            self._send_json({"success": False, "error": str(exc)}, status=500)

    def do_POST(self):
        global CURRENT_ACTIVE_VIDEO, UPLOADED_SOURCE_PATH, EXPORT_STATE, EXPORT_CANCEL_EVENT
        content_length = int(self.headers.get('Content-Length', 0))
        path = urllib.parse.urlparse(self.path).path
        if path.startswith('/api/') and not self._check_local_origin():
            return
        if path == '/api/upload_source':
            self._handle_source_upload(content_length)
            return
        max_content_length = (
            20 * 1024 * 1024
            if path == '/api/cloud/projects/save'
            else 2 * 1024 * 1024
        )
        if content_length > max_content_length:
            error_message = (
                "حجم بيانات المشروع يتجاوز الحد المسموح للمزامنة."
                if path == '/api/cloud/projects/save'
                else "حجم بيانات الطلب يتجاوز الحد المسموح."
            )
            self._send_json({
                "success": False,
                "error": error_message,
            }, status=413)
            return

        try:
            body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else '{}'
            data = json.loads(body) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json({"success": False, "error": f"طلب JSON غير صالح: {exc}"}, status=400)
            return

        if path == '/api/cancel_export':
            with SOURCE_EXPORT_LOCK:
                if EXPORT_STATE.get("status") != "exporting" or EXPORT_CANCEL_EVENT is None:
                    self._send_json({
                        "success": False,
                        "error": "لا توجد عملية تصدير قيد التشغيل لإلغائها.",
                    }, status=409)
                    return
                EXPORT_STATE["status"] = "cancelling"
                EXPORT_STATE["stage"] = "cancelling"
                EXPORT_STATE["message"] = "جارٍ إيقاف عملية FFmpeg وتنظيف الملفات المؤقتة..."
                EXPORT_CANCEL_EVENT.set()
            self._send_json({"success": True, "message": "تم طلب إلغاء التصدير."}, status=202)
            return

        if (
            path.startswith('/api/auth/')
            or path.startswith('/api/cloud/')
            or path == '/api/activate_source'
        ):
            if not self._check_local_origin():
                return

        if path == '/api/auth/google/start':
            try:
                authorization_url = CLOUD_AUTH.begin_google_login()
                if not webbrowser.open(authorization_url, new=2):
                    raise CloudAuthError("تعذر فتح المتصفح الخارجي لتسجيل الدخول.")
                self._send_json({"success": True})
            except CloudAuthError as exc:
                self._send_cloud_error(exc)
            return

        if path == '/api/activate_source':
            source_path = data.get('path')
            if not isinstance(source_path, str) or not os.path.isfile(source_path):
                self._send_json({"success": False, "error": "ملف الفيديو غير موجود."}, status=404)
                return
            source_path = os.path.abspath(source_path)
            with SOURCE_EXPORT_LOCK:
                if EXPORT_STATE["status"] == "exporting":
                    self._send_json({"success": False, "error": "انتظر انتهاء التصدير قبل تغيير مصدر الفيديو."}, status=409)
                    return
                if not os.path.isfile(source_path):
                    self._send_json({"success": False, "error": "ملف الفيديو غير موجود."}, status=404)
                    return
                if UPLOADED_SOURCE_PATH and UPLOADED_SOURCE_PATH != source_path:
                    remove_uploaded_source(UPLOADED_SOURCE_PATH)
                    UPLOADED_SOURCE_PATH = None
                CURRENT_ACTIVE_VIDEO = source_path
                EXPORT_STATE = {
                    "status": "idle",
                    "progress": 0,
                    "message": "",
                    "output_path": ""
                }
                self._send_json({
                    "success": True,
                    "path": source_path,
                    "size_bytes": os.path.getsize(source_path),
                })
            return

        if path == '/api/auth/logout':
            try:
                CLOUD_AUTH.logout()
                self._send_json({"success": True})
            except CloudAuthError as exc:
                self._send_cloud_error(exc)
            return

        if path == '/api/cloud/projects/save':
            try:
                self._require_cloud_user()
                project = data.get("project")
                if not isinstance(project, dict) or not isinstance(project.get("state"), dict):
                    raise ValueError("بيانات المشروع غير صالحة.")
                result = CLOUD_PROJECTS.save_project(project, data.get("base_version"))
                self._send_json({"success": True, "project": result})
            except (CloudAuthError, CloudRequestError, ProjectConflict, ValueError) as exc:
                self._send_cloud_error(exc)
            return

        if path == '/api/cloud/media/start':
            try:
                self._require_cloud_user()
                project_id = data.get("project_id")
                if not isinstance(project_id, str) or not CURRENT_ACTIVE_VIDEO:
                    raise ValueError("حمّل فيديو واربطه بالمشروع قبل بدء النسخ السحابي.")
                if EXPORT_STATE["status"] == "exporting":
                    raise ValueError("انتظر انتهاء التصدير قبل رفع الفيديو.")
                filename = data.get("filename") or os.path.basename(CURRENT_ACTIVE_VIDEO)
                content_type = data.get("content_type") or "application/octet-stream"
                result = CLOUD_MEDIA.start_upload(
                    project_id,
                    CURRENT_ACTIVE_VIDEO,
                    filename,
                    content_type,
                )
                self._send_json({"success": True, "upload": result})
            except (CloudAuthError, CloudRequestError, MediaUploadError, ValueError) as exc:
                self._send_cloud_error(exc)
            return

        if path == '/api/cloud/media/control':
            try:
                session = self._require_cloud_user()
                upload_id = data.get("upload_id")
                action = data.get("action")
                if not isinstance(upload_id, str) or not isinstance(action, str):
                    raise ValueError("بيانات التحكم بالرفع غير صالحة.")
                result = CLOUD_MEDIA.control(upload_id, session["user_id"], action)
                self._send_json({"success": True, "upload": result})
            except (CloudAuthError, CloudRequestError, MediaUploadError, ValueError) as exc:
                self._send_cloud_error(exc)
            return

        if path == '/api/cloud/projects/delete':
            try:
                self._require_cloud_user()
                project_id = data.get("project_id")
                if not isinstance(project_id, str):
                    raise ValueError("معرف المشروع غير صالح.")
                CLOUD_MEDIA.delete_project_assets(project_id)
                CLOUD_PROJECTS.delete_project(project_id)
                self._send_json({"success": True})
            except (CloudAuthError, CloudRequestError, ValueError) as exc:
                self._send_cloud_error(exc)
            return

        if path == '/api/cloud/projects/duplicate':
            try:
                self._require_cloud_user()
                project_id = data.get("project_id")
                project = CLOUD_PROJECTS.get_project(project_id) if isinstance(project_id, str) else None
                if not project:
                    raise ValueError("المشروع غير موجود أو لا يمكن الوصول إليه.")
                project["id"] = str(uuid.uuid4())
                project["name"] = f"{project['name']} (نسخة)"
                project["media_status"] = "local_only"
                project["media_size_bytes"] = 0
                result = CLOUD_PROJECTS.save_project(project)
                self._send_json({"success": True, "project": result})
            except (CloudAuthError, CloudRequestError, ValueError) as exc:
                self._send_cloud_error(exc)
            return

        if path == '/api/remove_video':
            with SOURCE_EXPORT_LOCK:
                if EXPORT_STATE["status"] == "exporting":
                    self._send_json({"success": False, "error": "انتظر انتهاء التصدير قبل إزالة الفيديو."}, status=409)
                    return

                uploaded_source = UPLOADED_SOURCE_PATH
                CURRENT_ACTIVE_VIDEO = None
                UPLOADED_SOURCE_PATH = None
                EXPORT_STATE = {
                    "status": "idle",
                    "progress": 0,
                    "message": "",
                    "output_path": ""
                }
                remove_uploaded_source(uploaded_source)
                self._send_json({"success": True})
        elif path == '/api/select_video_native':
            with SOURCE_EXPORT_LOCK:
                if EXPORT_STATE["status"] == "exporting":
                    self._send_json({"success": False, "error": "انتظر انتهاء التصدير قبل تحميل مصدر آخر."}, status=409)
                    return
            file_path = run_dialog_on_main(
                filedialog.askopenfilename,
                title="اختر ملف الفيديو لتحليله ومونتاجه",
                filetypes=[("Video Files", "*.mp4 *.mov *.mkv *.webm *.avi"), ("All Files", "*.*")]
            )
            if file_path:
                with SOURCE_EXPORT_LOCK:
                    if EXPORT_STATE["status"] == "exporting":
                        self._send_json({"success": False, "error": "انتظر انتهاء التصدير قبل تحميل مصدر آخر."}, status=409)
                        return
                    CURRENT_ACTIVE_VIDEO = os.path.abspath(file_path)
                    remove_uploaded_source(UPLOADED_SOURCE_PATH)
                    UPLOADED_SOURCE_PATH = None
                    size_mb = round(os.path.getsize(CURRENT_ACTIVE_VIDEO) / (1024 * 1024), 1)
                    self._send_json({
                        "success": True,
                        "path": CURRENT_ACTIVE_VIDEO,
                        "name": os.path.basename(CURRENT_ACTIVE_VIDEO),
                        "size_mb": size_mb,
                        "size_bytes": os.path.getsize(CURRENT_ACTIVE_VIDEO),
                        "stream_url": f"/api/stream_video?path={urllib.parse.quote(CURRENT_ACTIVE_VIDEO)}"
                    })
            else:
                self._send_json({"success": False})

        # اختيار مجلد الحفظ
        elif path == '/api/browse_folder':
            folder = run_dialog_on_main(filedialog.askdirectory, title="اختر مجلد حفظ الفيديو الممنتج")
            self._send_json({"folder": folder if folder else None})

        # فتح المجلد
        elif path == '/api/open_folder':
            folder = data.get('folder', '')
            if os.path.isdir(folder):
                os.startfile(folder)
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": "المجلد غير موجود"})

        # فحص وجود الملف
        elif path == '/api/check_file_exists':
            path = data.get('path', '')
            self._send_json({"exists": os.path.exists(path) if path else False})

        elif path == '/api/export_cuts':
            full_path = data.get('full_path', '')
            if not isinstance(full_path, str) or not full_path.strip():
                self._send_json({"success": False, "error": "مسار حفظ بيانات القص غير محدد."}, status=400)
                return
            try:
                settings = ExportSettings.from_dict(data.get("settings"))
                validated_segments = apply_export_range(
                    validate_export_segments(data.get('segments')),
                    settings,
                )
                with SOURCE_EXPORT_LOCK:
                    source_video = CURRENT_ACTIVE_VIDEO
                    if not source_video or not os.path.isfile(source_video):
                        raise ValueError("تعذر العثور على الفيديو الأصلي لقراءة معدل الإطارات.")
                    media_info = MediaProbe.probe_file(source_video)
                    if not media_info:
                        raise ValueError("تعذر قراءة معدل إطارات الفيديو الأصلي.")
                base_path = os.path.abspath(full_path)
                if os.path.splitext(base_path)[1].lower() in (".json", ".edl"):
                    base_path = os.path.splitext(base_path)[0]
                os.makedirs(os.path.dirname(base_path) or ".", exist_ok=True)
                json_path = base_path + ".json"
                edl_path = base_path + ".edl"
                overwrite = data.get("overwrite_confirmed") is True
                if not overwrite and (os.path.exists(json_path) or os.path.exists(edl_path)):
                    self._send_json({
                        "success": False,
                        "error": "يوجد ملف JSON أو EDL سابق؛ أكّد الاستبدال أو اختر اسمًا آخر.",
                    }, status=409)
                    return

                cuts = []
                timeline_position = 0.0
                edl_lines = ["TITLE: AUTO SILENCE CUTTER", "FCM: NON-DROP FRAME", ""]
                for index, segment in enumerate(validated_segments, start=1):
                    duration = segment["end"] - segment["start"]
                    cuts.append({
                        "order": index,
                        "clip_id": segment.get("id") or f"clip_{index:03d}",
                        "source_in": segment["start"],
                        "source_out": segment["end"],
                        "timeline_in": timeline_position,
                        "timeline_out": timeline_position + duration,
                        "duration": duration,
                    })
                    edl_lines.extend([
                        (
                            f"{index:03d}  AX       V     C        "
                            f"{format_timecode(segment['start'], media_info.fps)} "
                            f"{format_timecode(segment['end'], media_info.fps)} "
                            f"{format_timecode(timeline_position, media_info.fps)} "
                            f"{format_timecode(timeline_position + duration, media_info.fps)}"
                        ),
                        f"* SOURCE: {data.get('source_name', '')}",
                        "",
                    ])
                    timeline_position += duration
                csv_lines = [["Order", "Clip ID", "Source In (s)", "Source Out (s)", "Timeline In (s)", "Timeline Out (s)", "Duration (s)"]]
                csv_lines.extend([
                    [
                        item["order"], item["clip_id"], item["source_in"],
                        item["source_out"], item["timeline_in"], item["timeline_out"],
                        item["duration"],
                    ]
                    for item in cuts
                ])
                import io
                csv_buffer = io.StringIO(newline="")
                csv.writer(csv_buffer).writerows(csv_lines)
                json_path = base_path + ".json"
                edl_path = base_path + ".edl"
                csv_path = base_path + ".csv"
                _commit_text_files({
                    json_path: json.dumps({
                        "source_name": data.get("source_name", ""),
                        "fps": media_info.fps,
                        "duration_seconds": timeline_position,
                        "cuts": cuts,
                    }, ensure_ascii=False, indent=2),
                    edl_path: "\n".join(["TITLE: AUTO SILENCE CUTTER", "FCM: NON-DROP FRAME", ""])
                    + "\n".join(edl_lines[3:]),
                    csv_path: csv_buffer.getvalue(),
                }, overwrite)
                self._send_json({
                    "success": True,
                    "json_path": json_path,
                    "edl_path": edl_path,
                    "csv_path": csv_path,
                })
            except (KeyError, TypeError, ValueError, OSError) as exc:
                status = 409 if isinstance(exc, FileExistsError) else 400
                self._send_json({"success": False, "error": str(exc)}, status=status)

        # بدء عملية التصدير في Background Worker
        elif path == '/api/export_video':
            full_path = data.get('full_path', '')
            segments = data.get('segments', [])
            export_type = data.get('export_type', 'video')

            if not isinstance(full_path, str) or not full_path.strip():
                self._send_json({"success": False, "error": "مسار الحفظ غير محدد."}, status=400)
                return

            try:
                settings_payload = data.get("settings")
                if settings_payload is None and data.get("format"):
                    settings_payload = {"format": data["format"]}
                settings = ExportSettings.from_dict(settings_payload)
                if data.get("format") and data["format"] != settings.format:
                    raise ValueError("صيغة الطلب لا تطابق صيغة إعدادات التصدير.")
                validated_segments = apply_export_range(
                    validate_export_segments(segments),
                    settings,
                )
            except (KeyError, TypeError, ValueError, ExportSettingsError) as exc:
                self._send_json({"success": False, "error": str(exc)}, status=400)
                return

            package_dir = None
            if export_type == "capcut":
                package_dir = os.path.abspath(full_path)
                full_path = os.path.join(package_dir, f"rendered_video.{settings.format}")
            elif export_type != "video":
                self._send_json({"success": False, "error": "نوع تصدير الفيديو غير مدعوم."}, status=400)
                return
            else:
                full_path = os.path.abspath(full_path)

            overwrite = data.get("overwrite_confirmed") is True
            target_exists = os.path.exists(package_dir or full_path)
            if target_exists and not overwrite:
                self._send_json({
                    "success": False,
                    "error": "يوجد ملف أو مجلد سابق؛ أكّد الاستبدال أو اختر اسمًا آخر.",
                }, status=409)
                return

            with SOURCE_EXPORT_LOCK:
                if EXPORT_STATE["status"] in ("exporting", "cancelling"):
                    self._send_json({"success": False, "error": "هناك عملية تصدير قيد التشغيل بالفعل."}, status=409)
                    return
                source_video = CURRENT_ACTIVE_VIDEO
                if not source_video or not os.path.isfile(source_video):
                    self._send_json({"success": False, "error": "تعذر العثور على الفيديو الأصلي."}, status=400)
                    return

                EXPORT_CANCEL_EVENT = threading.Event()
                EXPORT_STATE = {
                    "status": "exporting",
                    "progress": 0,
                    "message": "Preparing Timeline",
                    "output_path": full_path,
                    "stage": "preparing",
                    "elapsed_seconds": 0,
                    "estimated_remaining_seconds": None,
                    "fps": None,
                    "speed": None,
                    "output_size_bytes": None,
                }

                try:
                    threading.Thread(
                        target=worker_ffmpeg_export,
                        args=(
                            source_video,
                            validated_segments,
                            full_path,
                            package_dir,
                            data.get("source_name", ""),
                            settings,
                            overwrite,
                            EXPORT_CANCEL_EVENT,
                        ),
                        daemon=True
                    ).start()
                except RuntimeError as exc:
                    EXPORT_STATE = {
                        "status": "error",
                        "progress": 0,
                        "message": f"تعذر بدء التصدير: {exc}",
                        "output_path": full_path,
                        "stage": "error",
                    }
                    EXPORT_CANCEL_EVENT = None
                    self._send_json({"success": False, "error": EXPORT_STATE["message"]}, status=500)
                    return

            self._send_json({"success": True, "message": "تم بدء التصدير في الخلفية"}, status=202)

        else:
            self.send_error(404, "Endpoint not found")

    def _stream_file(self, file_path):
        size = os.path.getsize(file_path)
        content_type = video_content_type(file_path)
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get('Range')
        if range_header:
            try:
                start, end = parse_byte_range(range_header, size)
                status = 206
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with open(file_path, 'rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def worker_ffmpeg_export(
    input_file,
    kept_segments,
    output_file,
    package_dir=None,
    source_name="",
    settings=None,
    overwrite=False,
    cancel_event=None,
):
    """Render the immutable request snapshot and publish only verified output."""
    global EXPORT_STATE, EXPORT_CANCEL_EVENT
    output_file = os.path.abspath(output_file)
    started_at = time.monotonic()
    settings = settings or ExportSettings()

    def publish_progress(progress):
        elapsed = max(0, time.monotonic() - started_at)
        percent = max(0, min(99, int(progress.get("percent") or 0)))
        remaining = elapsed * (100 - percent) / percent if percent > 0 else None
        stage = progress.get("stage", "encoding")
        messages = {
            "processing_clips": "Processing video clips",
            "encoding": "Encoding video and audio",
            "finalizing": "Finalizing and validating file",
        }
        with SOURCE_EXPORT_LOCK:
            if EXPORT_STATE.get("status") not in ("exporting", "cancelling"):
                return
            EXPORT_STATE.update({
                "progress": percent,
                "stage": stage,
                "message": messages.get(stage, "Processing export"),
                "elapsed_seconds": round(elapsed, 1),
                "estimated_remaining_seconds": (
                    round(remaining, 1) if remaining is not None else None
                ),
                "fps": progress.get("fps"),
                "speed": progress.get("speed"),
                "output_size_bytes": progress.get("output_size_bytes"),
            })

    try:
        if not os.path.isfile(input_file):
            raise FileNotFoundError("ملف الفيديو الأصلي غير موجود.")
        if not kept_segments:
            raise ValueError("لا توجد مقاطع للتصدير.")
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("تم إلغاء التصدير.")

        with SOURCE_EXPORT_LOCK:
            EXPORT_STATE.update({
                "stage": "processing_video",
                "message": "Preparing Timeline",
            })

        if package_dir:
            success, message = CapCutPackageExporter.export_segments(
                input_file,
                kept_segments,
                package_dir,
                settings,
                source_name=source_name,
                progress_callback=publish_progress,
                cancel_event=cancel_event,
                overwrite=overwrite,
            )
        else:
            success, message = FFmpegRenderer.render_segments(
                input_file,
                [(float(item["start"]), float(item["end"])) for item in kept_segments],
                output_file,
                settings=settings,
                progress_callback=publish_progress,
                cancel_event=cancel_event,
                overwrite=overwrite,
            )
        if not success:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("تم إلغاء التصدير.")
            raise RuntimeError(message)

        if package_dir:
            output_file = os.path.join(package_dir, f"rendered_video.{settings.format}")
        if not os.path.isfile(output_file) or os.path.getsize(output_file) <= 0:
            raise RuntimeError("لم يتم العثور على ملف التصدير النهائي بعد التحقق.")

        elapsed = max(0, time.monotonic() - started_at)
        with SOURCE_EXPORT_LOCK:
            EXPORT_STATE = {
                "status": "completed",
                "progress": 100,
                "message": message,
                "output_path": output_file,
                "stage": "completed",
                "elapsed_seconds": round(elapsed, 1),
                "estimated_remaining_seconds": 0,
                "fps": EXPORT_STATE.get("fps"),
                "speed": EXPORT_STATE.get("speed"),
                "output_size_bytes": os.path.getsize(output_file),
            }
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            status = "cancelled"
            message = "تم إلغاء التصدير وتنظيف الملفات المؤقتة."
            stage = "cancelled"
        else:
            logger.exception("Video export failed")
            status = "error"
            message = str(exc)
            stage = "error"
        with SOURCE_EXPORT_LOCK:
            EXPORT_STATE = {
                "status": status,
                "progress": EXPORT_STATE.get("progress", 0),
                "message": message,
                "output_path": output_file,
                "stage": stage,
                "elapsed_seconds": round(max(0, time.monotonic() - started_at), 1),
                "estimated_remaining_seconds": None,
                "fps": EXPORT_STATE.get("fps"),
                "speed": EXPORT_STATE.get("speed"),
                "output_size_bytes": None,
            }
    finally:
        with SOURCE_EXPORT_LOCK:
            if EXPORT_CANCEL_EVENT is cancel_event:
                EXPORT_CANCEL_EVENT = None


def run_server():
    server = ThreadingHTTPServer(('127.0.0.1', PORT), CustomHandler)
    server.serve_forever()


if __name__ == '__main__':
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    print(f"[*] محرك التصدير المكتبي يعمل بنجاح على: http://127.0.0.1:{PORT}/")
    
    url = f"http://127.0.0.1:{PORT}/"
    edge = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
    chrome = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    
    if os.path.exists(edge):
        subprocess.Popen([edge, f'--app={url}', '--window-size=1380,880'])
    elif os.path.exists(chrome):
        subprocess.Popen([chrome, f'--app={url}', '--window-size=1380,880'])
    else:
        webbrowser.open(url)

    try:
        # الـ main thread هو اللي بيخدم نوافذ tkinter ويفضل يحدّث root
        while True:
            try:
                func, args, kwargs, box, done = DIALOG_QUEUE.get(timeout=0.05)
            except queue.Empty:
                try:
                    root.update()
                except tk.TclError:
                    pass
                continue
            try:
                box["result"] = func(*args, **kwargs)
            except Exception as exc:
                box["error"] = exc
            finally:
                done.set()
    except KeyboardInterrupt:
        sys.exit(0)

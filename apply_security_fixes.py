"""
apply_security_fixes.py

يطبّق إصلاحات الأمان على desktop_app.py:
  1. فحص الـ Origin لكل مسارات /api/
  2. translate_path آمن (يمنع .env و .git والكود وأي ..)
  3. stream_video للفيديو النشط فقط + دعم Range
  4. open_folder يقبل مجلدات فقط

التشغيل (من داخل فولدر المشروع):
    python -X utf8 apply_security_fixes.py

بيعمل نسخة احتياطية desktop_app.py.bak قبل أي تعديل،
ولو أي جزء مش موجود بنفس الشكل بيوقف من غير ما يعدّل الملف.
"""
import ast
import os
import re
import shutil
import sys

TARGET = "desktop_app.py"

# ---------------------------------------------------------------- كود جديد

CONSTANTS = r'''PORT = 8990

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
'''

NEW_TRANSLATE = r'''    def translate_path(self, path):
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
'''

GET_CHECK = r'''        request = urllib.parse.urlparse(self.path)
        if request.path.startswith('/api/') and not self._check_local_origin():
            return
'''

NEW_STREAM = r'''        if request.path == '/api/stream_video':
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
'''

STREAM_METHOD = r'''    def _stream_file(self, file_path):
        size = os.path.getsize(file_path)
        content_type = mimetypes.guess_type(file_path)[0] or 'video/mp4'
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get('Range')
        if range_header and range_header.startswith('bytes='):
            try:
                first, _, last = range_header[6:].split(',')[0].strip().partition('-')
                if first == '':
                    start = max(size - int(last), 0)
                else:
                    start = int(first)
                    if last:
                        end = min(int(last), size - 1)
                if start > end or start >= size:
                    raise ValueError
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

'''

POST_CHECK = r'''        path = urllib.parse.urlparse(self.path).path
        if path.startswith('/api/') and not self._check_local_origin():
            return
        if path == '/api/upload_source':'''


# ---------------------------------------------------------------- أدوات

def fail(message):
    print(f"[X] {message}")
    print("    لم يتم تعديل أي ملف.")
    sys.exit(1)


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        fail(f"تعذر العثور على الجزء: {label} (موجود {text.count(old)} مرة)")
    print(f"[+] {label}")
    return text.replace(old, new, 1)


def main():
    if not os.path.isfile(TARGET):
        fail(f"الملف {TARGET} غير موجود. شغّل السكريبت من فولدر المشروع.")

    with open(TARGET, "rb") as f:
        raw = f.read()
    uses_crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")

    if "_stream_file" in text:
        print("[=] التعديلات مطبقة بالفعل. مفيش حاجة أعملها.")
        return

    # 1) import mimetypes
    text = replace_once(text, "import uuid\n", "import uuid\nimport mimetypes\n", "import mimetypes")

    # 2) الثوابت
    text = replace_once(text, "PORT = 8990\n", CONSTANTS, "STATIC_EXTENSIONS / BLOCKED_DIRS")

    # 3) translate_path
    pattern = re.compile(
        r"    def translate_path\(self, path\):\n.*?"
        r"        return os\.path\.join\(BASE_DIR, clean_path\.lstrip\('/'\)\)\n",
        re.DOTALL,
    )
    if len(pattern.findall(text)) != 1:
        fail("تعذر العثور على translate_path")
    text = pattern.sub(lambda m: NEW_TRANSLATE, text, count=1)
    print("[+] translate_path آمن")

    # 4) فحص Origin في do_GET
    text = replace_once(
        text,
        "    def do_GET(self):\n        request = urllib.parse.urlparse(self.path)\n",
        "    def do_GET(self):\n" + GET_CHECK,
        "فحص Origin في do_GET",
    )

    # 5) stream_video
    pattern = re.compile(
        r"        if request\.path == '/api/stream_video':\n.*?"
        r"self\.send_error\(404, \"الفيديو غير موجود\"\)\n                return\n",
        re.DOTALL,
    )
    if len(pattern.findall(text)) != 1:
        fail("تعذر العثور على بلوك stream_video")
    text = pattern.sub(lambda m: NEW_STREAM, text, count=1)
    print("[+] stream_video (الفيديو النشط فقط)")

    # 6) دالة _stream_file
    text = replace_once(
        text,
        "    def _send_json(self, data, status=200):\n",
        STREAM_METHOD + "    def _send_json(self, data, status=200):\n",
        "دالة _stream_file (Range)",
    )

    # 7) فحص Origin في do_POST
    text = replace_once(
        text,
        "        path = urllib.parse.urlparse(self.path).path\n"
        "        if path == '/api/upload_source':",
        POST_CHECK,
        "فحص Origin في do_POST",
    )

    # 8) open_folder
    text = replace_once(
        text,
        "            if os.path.exists(folder):\n                os.startfile(folder)",
        "            if os.path.isdir(folder):\n                os.startfile(folder)",
        "open_folder للمجلدات فقط",
    )

    # تأكد إن الناتج Python سليم قبل الكتابة
    try:
        ast.parse(text)
    except SyntaxError as exc:
        fail(f"الناتج فيه خطأ syntax ({exc}).")

    shutil.copy2(TARGET, TARGET + ".bak")
    if uses_crlf:
        text = text.replace("\n", "\r\n")
    with open(TARGET, "wb") as f:
        f.write(text.encode("utf-8"))

    print()
    print(f"[OK] تم. النسخة الاحتياطية: {TARGET}.bak")
    print("     شغّل التطبيق وجرّب: تحميل فيديو، تشغيله، التقديم فيه، والتصدير.")


if __name__ == "__main__":
    main()

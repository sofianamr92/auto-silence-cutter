"""
apply_tk_fix.py

يحل مشكلة tkinter في desktop_app.py:
  نوافذ اختيار الفيديو/المجلد كانت بتتفتح من threads السيرفر
  (tkinter مش thread-safe → تعليق أو crash عشوائي).
  دلوقتي بتتفتح من الـ main thread عبر queue.

التشغيل (من فولدر المشروع):
    python -X utf8 apply_tk_fix.py

بيعمل نسخة احتياطية desktop_app.py.bak2 ويوقف من غير تعديل لو أي جزء مش موجود.
"""
import ast
import os
import re
import shutil
import sys

TARGET = "desktop_app.py"

DIALOG_HELPER = r'''root.attributes('-topmost', True)

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
'''

NEW_MAIN_LOOP = r'''    try:
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
'''


def fail(message):
    print(f"[X] {message}")
    print("    لم يتم تعديل أي ملف.")
    sys.exit(1)


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        fail(f"تعذر العثور على: {label} (موجود {text.count(old)} مرة)")
    print(f"[+] {label}")
    return text.replace(old, new, 1)


def main():
    if not os.path.isfile(TARGET):
        fail(f"الملف {TARGET} غير موجود. شغّل السكريبت من فولدر المشروع.")

    with open(TARGET, "rb") as f:
        raw = f.read()
    uses_crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")

    if "run_dialog_on_main" in text:
        print("[=] التعديل مطبق بالفعل. مفيش حاجة أعملها.")
        return

    text = replace_once(text, "import threading\n", "import queue\nimport threading\n", "import queue")

    text = replace_once(
        text, "root.attributes('-topmost', True)\n", DIALOG_HELPER, "run_dialog_on_main + DIALOG_QUEUE"
    )

    text = replace_once(
        text,
        "file_path = filedialog.askopenfilename(",
        "file_path = run_dialog_on_main(\n                filedialog.askopenfilename,",
        "نافذة اختيار الفيديو",
    )

    text = replace_once(
        text,
        "folder = filedialog.askdirectory(",
        "folder = run_dialog_on_main(filedialog.askdirectory, ",
        "نافذة اختيار المجلد",
    )

    pattern = re.compile(
        r"    try:\n        while True:\n            threading\.Event\(\)\.wait\(1\)\n"
        r"    except KeyboardInterrupt:\n        sys\.exit\(0\)\n?"
    )
    if len(pattern.findall(text)) != 1:
        fail("تعذر العثور على حلقة الانتظار في آخر الملف")
    text = pattern.sub(lambda m: NEW_MAIN_LOOP, text, count=1)
    print("[+] حلقة الـ main thread بتخدم النوافذ")

    try:
        ast.parse(text)
    except SyntaxError as exc:
        fail(f"الناتج فيه خطأ syntax ({exc}).")

    shutil.copy2(TARGET, TARGET + ".bak2")
    if uses_crlf:
        text = text.replace("\n", "\r\n")
    with open(TARGET, "wb") as f:
        f.write(text.encode("utf-8"))

    print()
    print(f"[OK] تم. النسخة الاحتياطية: {TARGET}.bak2")
    print("     جرّب: زرار اختيار الفيديو، وزرار اختيار مجلد الحفظ (كذا مرة ورا بعض).")


if __name__ == "__main__":
    main()

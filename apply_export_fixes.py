"""
apply_export_fixes.py

إصلاحات على محرك التصدير الجديد (من فولدر المشروع):

  core/editing_engine.py
    1. الفلتر الطويل بيتكتب في ملف ويتمرر لـ FFmpeg بدل سطر الأوامر
       (Windows حده حوالي 32,767 حرف = حوالي 110-195 مقطع)
    2. fade صوتي 10ms على حدود كل مقطع (يمنع التكّات)
    3. الحفظ من غير استبدال بيشتغل على فلاشات FAT32/exFAT
       (os.link مش مدعوم هناك → fallback لإعادة التسمية)
    4. دالة merge_contiguous_segments

  desktop_app.py
    5. دمج المقاطع المتلاصقة قبل التصدير (عشان الـ fade ما يعملش نقرة
       وسط كلام متواصل)
    6. نفس fallback الـ hard link لملفات JSON/EDL/CSV

  tests/test_export_command.py   (ملف جديد)
    7. اختبارات pytest للنقاط اللي فوق

التشغيل:
    python -X utf8 apply_export_fixes.py

بيعمل نسخ احتياطية (.bak4) ويوقف من غير تعديل لو أي جزء مش موجود بنفس الشكل.
"""
import ast
import os
import shutil
import sys

ENGINE = os.path.join("core", "editing_engine.py")
APP = "desktop_app.py"
TEST_FILE = os.path.join("tests", "test_export_command.py")


# ------------------------------------------------------------------ engine

MERGE_FUNC = '''def merge_contiguous_segments(segments, tolerance=0.002):
    """يدمج المقاطع المتلاصقة (بدون قص بينها) كي لا يظهر fade وسط كلام متواصل."""
    merged = []
    for segment in segments:
        item = dict(segment)
        if merged and abs(float(item["start"]) - float(merged[-1]["end"])) <= tolerance:
            merged[-1]["end"] = max(float(merged[-1]["end"]), float(item["end"]))
        else:
            merged.append(item)
    return merged


class FFmpegRenderer:
'''

FILTER_OPTION_HELPER = '''    @staticmethod
    @functools.lru_cache(maxsize=None)
    def _filter_script_option(ffmpeg: str) -> str:
        """يختار طريقة تمرير الفلتر من ملف: `-/filter_complex` (FFmpeg 7+) أو
        `-filter_complex_script` (الأقدم)، بتجربة فعلية مرة واحدة."""
        script_path = None
        try:
            descriptor, script_path = tempfile.mkstemp(prefix="auto_silence_probe_", suffix=".txt")
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write("color=c=black:s=16x16:d=0.1[out]")
            result = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-v", "error",
                    "-/filter_complex", script_path,
                    "-map", "[out]", "-f", "null", "-",
                ],
                capture_output=True,
                timeout=20,
            )
            if result.returncode == 0:
                return "-/filter_complex"
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            if script_path and os.path.exists(script_path):
                try:
                    os.remove(script_path)
                except OSError:
                    pass
        return "-filter_complex_script"

    @staticmethod
    def _aspect_filter(
'''

OLD_AUDIO = '''                audio_filter = (
                    f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},"
                    "asetpts=PTS-STARTPTS"
                )
'''

NEW_AUDIO = '''                # fade قصير على حدود المقطع يمنع "التكّات" عند نقط القص
                fade_len = min(0.010, (end - start) / 4)
                audio_filter = (
                    f"[0:a:0]atrim=start={start:.6f}:end={end:.6f},"
                    "asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={fade_len:.4f},"
                    f"afade=t=out:st={max(0.0, (end - start) - fade_len):.6f}:d={fade_len:.4f}"
                )
'''

OLD_COMMAND_START = '''        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-nostats",
'''

NEW_COMMAND_START = '''        filter_graph = ";".join(filter_parts)
        if filter_script_path:
            # الفلتر الطويل في ملف بدل سطر الأوامر (حد Windows ~32K حرف)
            with open(filter_script_path, "w", encoding="utf-8") as handle:
                handle.write(filter_graph)
            filter_args = [FFmpegRenderer._filter_script_option(ffmpeg), filter_script_path]
        else:
            filter_args = ["-filter_complex", filter_graph]

        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-nostats",
'''

OLD_FILTER_ARGS = '''            "-filter_complex",
            ";".join(filter_parts),
'''
NEW_FILTER_ARGS = '''            *filter_args,
'''

OLD_COMMIT = '''        except OSError as exc:
            raise OSError("تعذر إنشاء الملف النهائي دون استبدال ملف موجود.") from exc
        os.remove(temp_path)
'''

NEW_COMMIT = '''        except OSError as exc:
            # hard links غير مدعومة (exFAT / FAT32 / بعض الأقراص الشبكية): إعادة تسمية
            if os.path.exists(output_path):
                raise FileExistsError("يوجد ملف بهذا الاسم؛ أكّد الاستبدال أو اختر اسمًا آخر.") from exc
            try:
                os.rename(temp_path, output_path)
            except FileExistsError as exists_exc:
                raise FileExistsError("يوجد ملف بهذا الاسم؛ أكّد الاستبدال أو اختر اسمًا آخر.") from exists_exc
            except OSError as rename_exc:
                raise OSError("تعذر إنشاء الملف النهائي دون استبدال ملف موجود.") from rename_exc
            return
        os.remove(temp_path)
'''

OLD_FINALLY = '''            finally:
                if os.path.exists(temp_output):
                    os.remove(temp_output)
        except ExportCancelledError as exc:
'''

NEW_FINALLY = '''            finally:
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                if os.path.exists(filter_script_path):
                    try:
                        os.remove(filter_script_path)
                    except OSError:
                        pass
        except ExportCancelledError as exc:
'''

# ------------------------------------------------------------------ desktop_app

OLD_TEXT_LINK = '''            try:
                os.link(temp_path, path)
            except FileExistsError as exc:
                raise FileExistsError(f"يوجد ملف بهذا الاسم بالفعل: {path}") from exc
            created_paths.append(path)
            os.remove(temp_path)
'''

NEW_TEXT_LINK = '''            try:
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
'''

# ------------------------------------------------------------------ tests

TEST_CONTENT = '''# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
import sys
from fractions import Fraction

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.editing_engine import FFmpegRenderer, merge_contiguous_segments  # noqa: E402
from export.export_settings import ExportSettings  # noqa: E402


def _command(segments, script_path=None, include_audio=True):
    return FFmpegRenderer._build_configured_command(
        ffmpeg="ffmpeg",
        source_path="in.mp4",
        output_path="out.mp4",
        segments=segments,
        settings=ExportSettings(),
        source_width=1920,
        source_height=1080,
        include_audio=include_audio,
        output_width=None,
        output_height=None,
        encoder="libx264",
        source_fps=Fraction(30, 1),
        filter_script_path=script_path,
    )


def test_many_segments_do_not_inflate_command_line(tmp_path, monkeypatch):
    monkeypatch.setattr(
        FFmpegRenderer, "_filter_script_option", staticmethod(lambda ffmpeg: "-filter_complex_script")
    )
    segments = [(i * 2.0, i * 2.0 + 1.5) for i in range(600)]
    script = tmp_path / "graph.txt"
    command = _command(segments, str(script))
    assert len(" ".join(command)) < 3000
    assert "-filter_complex_script" in command
    assert command[command.index("-filter_complex_script") + 1] == str(script)
    graph = script.read_text(encoding="utf-8")
    assert "concat=n=600:v=1:a=0[vout]" in graph
    assert "concat=n=600:v=0:a=1[aout]" in graph


def test_inline_filter_still_available_without_script_path():
    command = _command([(0.0, 1.0), (2.0, 3.0)])
    assert "-filter_complex" in command


def test_audio_segments_have_short_fades():
    command = _command([(1.0, 3.0)])
    graph = command[command.index("-filter_complex") + 1]
    assert "afade=t=in:st=0:d=0.0100" in graph
    assert "afade=t=out:st=1.990000:d=0.0100" in graph


def test_fade_is_never_longer_than_a_quarter_of_the_clip():
    command = _command([(0.0, 0.020)])
    graph = command[command.index("-filter_complex") + 1]
    assert "afade=t=in:st=0:d=0.0050" in graph


def test_no_audio_filters_without_audio():
    command = _command([(0.0, 1.0)], include_audio=False)
    graph = command[command.index("-filter_complex") + 1]
    assert "atrim" not in graph and "afade" not in graph


def test_merge_contiguous_segments():
    merged = merge_contiguous_segments([
        {"start": 0.0, "end": 2.0, "id": "a"},
        {"start": 2.0005, "end": 4.0},
        {"start": 5.0, "end": 6.0},
    ])
    assert merged == [
        {"start": 0.0, "end": 4.0, "id": "a"},
        {"start": 5.0, "end": 6.0},
    ]


def test_commit_output_falls_back_when_hard_links_unsupported(tmp_path, monkeypatch):
    temp = tmp_path / "temp.mp4"
    temp.write_bytes(b"video")
    target = tmp_path / "final.mp4"

    def no_link(*args, **kwargs):
        raise OSError("hard links not supported")

    monkeypatch.setattr(os, "link", no_link)
    FFmpegRenderer._commit_output(str(temp), str(target), overwrite=False)
    assert target.read_bytes() == b"video"
    assert not temp.exists()


def test_commit_output_never_overwrites_existing_file(tmp_path, monkeypatch):
    temp = tmp_path / "temp.mp4"
    temp.write_bytes(b"new")
    target = tmp_path / "final.mp4"
    target.write_bytes(b"old")

    def no_link(*args, **kwargs):
        raise OSError("hard links not supported")

    monkeypatch.setattr(os, "link", no_link)
    with pytest.raises(FileExistsError):
        FFmpegRenderer._commit_output(str(temp), str(target), overwrite=False)
    assert target.read_bytes() == b"old"


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg غير مثبت"
)
def test_render_hundreds_of_segments_end_to_end(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source),
        ],
        check=True,
    )
    segments = [(i * 0.5, i * 0.5 + 0.3) for i in range(55)]
    expected = sum(end - start for start, end in segments)
    output = tmp_path / "out.mp4"
    settings = ExportSettings(encoder_preset="ultrafast", hardware_acceleration="software")
    ok, message = FFmpegRenderer.render_segments(
        str(source), segments, str(output), settings=settings
    )
    assert ok, message
    assert output.is_file()
    assert not list(tmp_path.glob(".*.filter.txt"))
    probe = FFmpegRenderer.probe_media(str(output))
    assert abs(float(probe["format"]["duration"]) - expected) < 0.75
'''


# ------------------------------------------------------------------ helpers

def fail(message):
    print(f"[X] {message}")
    print("    لم يتم تعديل أي ملف.")
    sys.exit(1)


def read_text(path):
    if not os.path.isfile(path):
        fail(f"الملف {path} غير موجود. شغّل السكريبت من فولدر المشروع.")
    with open(path, "rb") as handle:
        raw = handle.read()
    return raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def write_text(path, text, crlf):
    shutil.copy2(path, path + ".bak4")
    if crlf:
        text = text.replace("\n", "\r\n")
    with open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        fail(f"تعذر العثور على: {label} (موجود {count} مرة)")
    print(f"[+] {label}")
    return text.replace(old, new, 1)


def main():
    engine, engine_crlf = read_text(ENGINE)
    app, app_crlf = read_text(APP)

    done = [
        "_filter_script_option" in engine,
        "merge_contiguous_segments" in engine,
        "merge_contiguous_segments" in app,
    ]
    if all(done):
        print("[=] التعديلات مطبقة بالفعل.")
        return
    if any(done):
        fail("بعض التعديلات مطبقة وبعضها لا. ارجع للنسخ .bak4 أو راجع الملفات يدوياً.")

    # ---- core/editing_engine.py
    engine = replace_once(
        engine,
        "import queue\nimport shutil\nimport subprocess\nimport threading\n",
        "import functools\nimport queue\nimport shutil\nimport subprocess\nimport tempfile\nimport threading\n",
        "imports (functools, tempfile)",
    )
    engine = replace_once(engine, "class FFmpegRenderer:\n", MERGE_FUNC, "دالة merge_contiguous_segments")
    engine = replace_once(
        engine,
        "            ffmpeg = FFmpegRenderer.get_ffmpeg_path()\n            capabilities = detect_ffmpeg_capabilities()\n",
        "            ffmpeg = FFmpegRenderer.get_ffmpeg_path()\n"
        "            filter_script_path = temp_output + \".filter.txt\"\n"
        "            capabilities = detect_ffmpeg_capabilities()\n",
        "مسار ملف الفلتر",
    )
    engine = replace_once(
        engine,
        "                encoder=encoder,\n                source_fps=source_fps,\n            )\n",
        "                encoder=encoder,\n                source_fps=source_fps,\n"
        "                filter_script_path=filter_script_path,\n            )\n",
        "تمرير مسار الفلتر لبناء الأمر",
    )
    engine = replace_once(engine, OLD_FINALLY, NEW_FINALLY, "تنظيف ملف الفلتر المؤقت")
    engine = replace_once(
        engine,
        "        source_fps: Fraction,\n    ) -> list:\n",
        "        source_fps: Fraction,\n        filter_script_path: Optional[str] = None,\n    ) -> list:\n",
        "باراميتر filter_script_path",
    )
    engine = replace_once(engine, OLD_AUDIO, NEW_AUDIO, "fade صوتي على حدود المقاطع")
    engine = replace_once(engine, OLD_COMMAND_START, NEW_COMMAND_START, "كتابة الفلتر في ملف")
    engine = replace_once(engine, OLD_FILTER_ARGS, NEW_FILTER_ARGS, "استخدام filter_args في الأمر")
    engine = replace_once(
        engine,
        "    @staticmethod\n    def _aspect_filter(\n",
        FILTER_OPTION_HELPER,
        "اختيار خيار الفلتر حسب إصدار FFmpeg",
    )
    engine = replace_once(engine, OLD_COMMIT, NEW_COMMIT, "fallback للحفظ بدون hard link")

    # ---- desktop_app.py
    app = replace_once(
        app,
        "from core.editing_engine import FFmpegRenderer\n",
        "from core.editing_engine import FFmpegRenderer, merge_contiguous_segments\n",
        "استيراد merge_contiguous_segments",
    )
    app = replace_once(
        app,
        "        validated.append(item)\n    return validated\n",
        "        validated.append(item)\n    return merge_contiguous_segments(validated)\n",
        "دمج المقاطع المتلاصقة قبل التصدير",
    )
    app = replace_once(app, OLD_TEXT_LINK, NEW_TEXT_LINK, "fallback الحفظ لملفات JSON/EDL/CSV")

    for name, code in ((ENGINE, engine), (APP, app)):
        try:
            ast.parse(code)
        except SyntaxError as exc:
            fail(f"{name}: خطأ syntax في الناتج ({exc}).")

    if os.path.exists(TEST_FILE):
        fail(f"{TEST_FILE} موجود بالفعل. احذفه أو غيّر اسمه ثم أعد التشغيل.")
    os.makedirs("tests", exist_ok=True)

    write_text(ENGINE, engine, engine_crlf)
    write_text(APP, app, app_crlf)
    with open(TEST_FILE, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(TEST_CONTENT)
    print(f"[+] اختبارات جديدة: {TEST_FILE}")

    print()
    print("[OK] تم. نسخ احتياطية: *.bak4")
    print("     شغّل الاختبارات:  python -m pytest tests/test_export_command.py -q")
    print("     وبعدها جرّب تصدير فيديو فيه مقاطع كتير.")


if __name__ == "__main__":
    main()

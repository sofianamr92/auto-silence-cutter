# -*- coding: utf-8 -*-
import os
import json
import csv
import shutil
import tempfile
from core.editing_engine import ProjectState
from core.editing_engine import FFmpegRenderer
from export.export_settings import ExportSettings

class CapCutPackageExporter:
    @staticmethod
    def export_segments(
        source_path: str,
        segments: list,
        target_dir: str,
        settings: ExportSettings,
        source_name: str = "",
        progress_callback=None,
        cancel_event=None,
        overwrite: bool = False,
    ):
        target_dir = os.path.abspath(target_dir)
        if os.path.exists(target_dir) and not overwrite:
            return False, "يوجد مجلد حزمة بهذا الاسم؛ أكّد الاستبدال أو اختر مجلدًا آخر."
        if not segments:
            return False, "لا توجد مقاطع صالحة لإنشاء حزمة القص."

        parent_dir = os.path.dirname(target_dir)
        os.makedirs(parent_dir, exist_ok=True)
        staging_dir = tempfile.mkdtemp(prefix=".auto-silence-package-", dir=parent_dir)
        clips_dir = os.path.join(staging_dir, "clips")
        metadata_dir = os.path.join(staging_dir, "metadata")
        os.makedirs(clips_dir)
        os.makedirs(metadata_dir)
        total_duration = sum(float(item["end"]) - float(item["start"]) for item in segments)
        if total_duration <= 0:
            shutil.rmtree(staging_dir)
            return False, "مدة المقاطع غير صالحة لإنشاء الحزمة."

        try:
            clip_rows = []
            completed_duration = 0.0
            extension = settings.format
            for index, segment in enumerate(segments, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("تم إلغاء التصدير.")
                source_in = float(segment["start"])
                source_out = float(segment["end"])
                duration = source_out - source_in
                clip_name = f"clip_{index:03d}.{extension}"
                clip_path = os.path.join(clips_dir, clip_name)

                def clip_progress(data):
                    if progress_callback:
                        clip_percent = float(data.get("percent") or 0) / 100
                        overall = 50 * (completed_duration + duration * clip_percent) / total_duration
                        progress_callback({
                            **data,
                            "percent": min(49, int(overall)),
                            "stage": "processing_clips",
                            "clip_index": index,
                            "clip_count": len(segments),
                        })

                success, message = FFmpegRenderer.render_segments(
                    source_path,
                    [(source_in, source_out)],
                    clip_path,
                    settings=settings,
                    progress_callback=clip_progress,
                    cancel_event=cancel_event,
                )
                if not success:
                    return False, message

                timeline_start = completed_duration
                completed_duration += duration
                clip_rows.append({
                    "order": index,
                    "clip_id": segment.get("id") or f"clip_{index:03d}",
                    "source_name": source_name or os.path.basename(source_path),
                    "source_in_seconds": source_in,
                    "source_out_seconds": source_out,
                    "timeline_start_seconds": timeline_start,
                    "timeline_end_seconds": completed_duration,
                    "duration_seconds": duration,
                    "filename": clip_name,
                })

            rendered_video = os.path.join(staging_dir, f"rendered_video.{extension}")

            def final_progress(data):
                if progress_callback:
                    overall = 50 + float(data.get("percent") or 0) * 0.49
                    progress_callback({
                        **data,
                        "percent": min(99, int(overall)),
                        "stage": "encoding",
                    })

            success, message = FFmpegRenderer.render_segments(
                source_path,
                [(float(item["start"]), float(item["end"])) for item in segments],
                rendered_video,
                settings=settings,
                progress_callback=final_progress,
                cancel_event=cancel_event,
            )
            if not success:
                return False, message

            with open(os.path.join(metadata_dir, "cuts.json"), "w", encoding="utf-8") as handle:
                import json
                json.dump({
                    "schema_version": 1,
                    "source_name": source_name or os.path.basename(source_path),
                    "format": settings.format,
                    "duration_seconds": total_duration,
                    "clips": clip_rows,
                    "compatibility_note": (
                        "ملفات التوقيت تساعد على استكمال العمل يدويًا؛ "
                        "لم يتم التحقق من استيرادها مباشرةً في CapCut Desktop."
                    ),
                }, handle, ensure_ascii=False, indent=2)
            with open(
                os.path.join(metadata_dir, "cuts.csv"),
                "w",
                newline="",
                encoding="utf-8-sig",
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "Order",
                    "Clip ID",
                    "Source",
                    "Source In (s)",
                    "Source Out (s)",
                    "Timeline Start (s)",
                    "Timeline End (s)",
                    "Duration (s)",
                    "Clip File",
                ])
                for row in clip_rows:
                    writer.writerow([
                        row["order"],
                        row["clip_id"],
                        row["source_name"],
                        row["source_in_seconds"],
                        row["source_out_seconds"],
                        row["timeline_start_seconds"],
                        row["timeline_end_seconds"],
                        row["duration_seconds"],
                        row["filename"],
                    ])
            with open(os.path.join(staging_dir, "README_AR.txt"), "w", encoding="utf-8") as handle:
                handle.write(
                    "حزمة Auto Silence Cutter\n"
                    "الفيديو النهائي داخل rendered_video، والمقاطع داخل clips، "
                    "وبيانات التوقيت داخل metadata.\n"
                    "لم يتم التحقق من توافق الاستيراد المباشر مع CapCut Desktop.\n"
                )

            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("تم إلغاء التصدير.")
            CapCutPackageExporter._commit_package(staging_dir, target_dir, overwrite)
            staging_dir = None
            return True, f"اكتمل إنشاء حزمة المقاطع.\nالمجلد: {target_dir}"
        except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            return False, str(exc)
        finally:
            if staging_dir and os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)

    @staticmethod
    def _commit_package(staging_dir: str, target_dir: str, overwrite: bool):
        backup_dir = None
        if os.path.exists(target_dir):
            if not overwrite:
                raise FileExistsError("يوجد مجلد حزمة بهذا الاسم؛ أكّد الاستبدال أو اختر مجلدًا آخر.")
            backup_dir = target_dir + f".backup-{os.getpid()}"
            if os.path.exists(backup_dir):
                raise FileExistsError("تعذر إنشاء نسخة مرحلية آمنة للحزمة الموجودة.")
            os.rename(target_dir, backup_dir)
        try:
            os.rename(staging_dir, target_dir)
        except OSError:
            if backup_dir and os.path.exists(backup_dir):
                os.rename(backup_dir, target_dir)
            raise
        if backup_dir:
            if os.path.isdir(backup_dir):
                shutil.rmtree(backup_dir)
            else:
                os.remove(backup_dir)

    @staticmethod
    def export_package(state: ProjectState, target_dir: str):
        pkg_dir = os.path.join(target_dir, "AUTO_SILENCE_CAPCUT_PACKAGE")
        os.makedirs(pkg_dir, exist_ok=True)
        meta_dir = os.path.join(pkg_dir, "metadata")
        os.makedirs(meta_dir, exist_ok=True)

        cuts_data = [
            {
                "id": c.id,
                "source_path": c.source_path,
                "source_in": c.source_in,
                "source_out": c.source_out,
                "timeline_in": c.timeline_in,
                "duration": c.duration
            }
            for c in state.clips if not c.is_cut
        ]
        with open(os.path.join(meta_dir, "cuts.json"), "w", encoding="utf-8") as f:
            json.dump(cuts_data, f, ensure_ascii=False, indent=2)

        with open(os.path.join(meta_dir, "cuts.csv"), "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Clip ID", "Source In (s)", "Source Out (s)", "Duration (s)"])
            for c in cuts_data:
                writer.writerow([c["id"], c["source_in"], c["source_out"], c["duration"]])

        readme_content = """حزمة المونتاج الذكية لبرنامج CapCut Desktop 8
===================================================
تحتوي هذه الحزمة على المقاطع المقصوصة بدقة وبيانات التوقيت الكاملة.
يمكنك استيراد المقاطع لخط زمني نظيف تماماً وخالٍ من السكتات والأنفاس.
"""
        with open(os.path.join(pkg_dir, "README_AR.txt"), "w", encoding="utf-8") as f:
            f.write(readme_content)

        source_path = state.media_info.filepath
        if source_path and os.path.isfile(source_path):
            copied_name = os.path.basename(source_path)
            shutil.copy2(source_path, os.path.join(pkg_dir, copied_name))
        return cuts_data

    @staticmethod
    def export_package_with_video(state: ProjectState, target_dir: str, resolution: str):
        cuts = CapCutPackageExporter.export_package(state, target_dir)
        pkg_dir = os.path.join(target_dir, "AUTO_SILENCE_CAPCUT_PACKAGE")
        output_path = os.path.join(pkg_dir, "rendered_video.mp4")
        segments = [
            (cut["source_in"], cut["source_out"])
            for cut in cuts
        ]
        success, message = FFmpegRenderer.render_segments(
            state.media_info.filepath,
            segments,
            output_path,
            resolution=resolution,
            fast_copy=(resolution == "الدقة الأصلية الكاملة"),
        )
        if not success:
            return False, message

        clips_dir = os.path.join(pkg_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)
        for index, cut in enumerate(cuts, start=1):
            clip_path = os.path.join(clips_dir, f"clip_{index:03d}.mp4")
            clip_success, clip_message = FFmpegRenderer.render_segments(
                state.media_info.filepath,
                [(cut["source_in"], cut["source_out"])],
                clip_path,
                resolution="الدقة الأصلية الكاملة",
                fast_copy=True,
            )
            if not clip_success:
                return False, clip_message
        return True, f"تم إنشاء حزمة CapCut والفيديو النهائي بنجاح.\nالمجلد: {pkg_dir}"

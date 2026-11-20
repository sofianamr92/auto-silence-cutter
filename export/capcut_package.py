# -*- coding: utf-8 -*-
import os
import json
import csv
import shutil
from core.editing_engine import ProjectState
from core.editing_engine import FFmpegRenderer

class CapCutPackageExporter:
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

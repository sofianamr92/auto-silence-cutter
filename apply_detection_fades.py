"""
apply_detection_fades.py

يحسّن جودة القص في 3 ملفات (من فولدر المشروع):

  AutoSilenceCutter.html
    - كشف السكتات بدقة 10ms بدل 1200 نقطة للفيديو كله
      (الفيديو 10 دقايق كان بيتقسم لقطع 0.5 ثانية!)
    - عتبة بالـ dB مع تقدير تلقائي لمستوى الضوضاء
    - إبقاء جزء طبيعي من الوقفة قبل وبعد الكلام (مش بيقص أول/آخر الكلمة)
    - 3 أنماط جاهزة: ريلز سريع / قياسي / هادئ، بقائمة جنب زرار القص

  core/editing_engine.py
    - fade صوتي 10ms على حدود كل مقطع (يمنع "التكّات")

  desktop_app.py
    - دمج المقاطع المتلاصقة قبل التصدير (عشان الـ fade ما يعملش
      نقرة في نص كلام متواصل)

التشغيل (من فولدر المشروع):
    python -X utf8 apply_detection_fades.py

بيعمل نسخة احتياطية (.bak3) لكل ملف، ولو أي جزء مش موجود بنفس الشكل
بيوقف من غير ما يعدّل أي ملف.
"""
import os
import re
import shutil
import sys

HTML = "AutoSilenceCutter.html"
ENGINE = os.path.join("core", "editing_engine.py")
APP = "desktop_app.py"

# ------------------------------------------------------------------ HTML / JS

JS_DECLARATIONS = r'''    let audioPeaks = [];
    // كشف السكتات بدقة عالية (إطار 10ms) منفصل عن نقاط رسم الموجة
    let silenceEnvelope = null;
    const DETECTION_PRESETS = {
      reels:    { thresholdDb: -38, minSilence: 0.30, keepTail: 0.08, keepLead: 0.10, minCut: 0.12 },
      standard: { thresholdDb: -38, minSilence: 0.45, keepTail: 0.12, keepLead: 0.15, minCut: 0.12 },
      relaxed:  { thresholdDb: -40, minSilence: 0.80, keepTail: 0.20, keepLead: 0.25, minCut: 0.12 }
    };
    let detectionPreset = (function() {
      const saved = localStorage.getItem('auto_silence_detect_preset');
      return Object.prototype.hasOwnProperty.call(DETECTION_PRESETS, saved) ? saved : 'standard';
    })();
'''

JS_ENVELOPE_CALL = r'''        try {
          silenceEnvelope = buildSilenceEnvelope(audioBuffer);
        } catch (envelopeError) {
          console.error('Silence envelope failed, using legacy detection:', envelopeError);
          silenceEnvelope = null;
        }

        let uploadWarning = '';
        sourceUploadReady = false;
'''

JS_DETECTION = r'''    // مغلّف الصوت: قيمة dB لكل 10ms (نافذة 20ms)
    function buildSilenceEnvelope(audioBuffer) {
      const sampleRate = audioBuffer.sampleRate;
      const hop = Math.max(1, Math.round(sampleRate * 0.01));
      const channelCount = Math.min(2, audioBuffer.numberOfChannels);
      const channels = [];
      for (let c = 0; c < channelCount; c++) channels.push(audioBuffer.getChannelData(c));
      const frames = Math.floor(channels[0].length / hop);
      if (frames < 3) return null;

      const energy = new Float32Array(frames);
      for (let f = 0; f < frames; f++) {
        const from = f * hop;
        let sum = 0;
        for (let c = 0; c < channelCount; c++) {
          const data = channels[c];
          for (let i = from; i < from + hop; i++) sum += data[i] * data[i];
        }
        energy[f] = sum / (hop * channelCount);
      }
      const db = new Float32Array(frames - 1);
      for (let f = 0; f < frames - 1; f++) {
        const rms = Math.sqrt((energy[f] + energy[f + 1]) / 2 + 1e-12);
        db[f] = 20 * Math.log10(rms + 1e-6);
      }
      return { db: db, hop: hop / sampleRate };
    }

    function detectSilences() {
      segments = [];
      if (!duration) return;
      if (!silenceEnvelope) {
        detectSilencesLegacy();
        return;
      }
      const preset = DETECTION_PRESETS[detectionPreset] || DETECTION_PRESETS.standard;
      const db = silenceEnvelope.db;
      const hop = silenceEnvelope.hop;
      const n = db.length;

      // مستوى الضوضاء = الشريحة 15% الأهدأ، ومش بيعدّي العتبة بأكتر من 6dB
      // (تحفّظ: قص كلام بالغلط أسوأ من ترك سكتة)
      const sample = [];
      for (let i = 0; i < n; i += 5) sample.push(db[i]);
      sample.sort(function(a, b) { return a - b; });
      const noiseFloor = sample.length ? sample[Math.floor(sample.length * 0.15)] : -60;
      const threshold = Math.min(
        Math.max(preset.thresholdDb, noiseFloor + 7),
        preset.thresholdDb + 6
      );

      const regions = [];
      let runStart = -1;
      for (let i = 0; i <= n; i++) {
        const silent = i < n && db[i] < threshold;
        if (silent && runStart < 0) {
          runStart = i;
        } else if (!silent && runStart >= 0) {
          regions.push([runStart * hop, i >= n ? duration : i * hop]);
          runStart = -1;
        }
      }

      const cutRanges = [];
      regions.forEach(function(region) {
        const s = region[0];
        const e = region[1];
        if (e - s < preset.minSilence) return;
        const atStart = s <= 0.001;
        const atEnd = e >= duration - 0.05;
        const cutStart = atStart ? 0 : s + preset.keepTail;
        const cutEnd = atEnd ? duration : e - preset.keepLead;
        if (cutEnd - cutStart >= preset.minCut) cutRanges.push([cutStart, cutEnd]);
      });

      let cursor = 0;
      cutRanges.forEach(function(range) {
        if (range[0] - cursor > 0.001) {
          segments.push({ start: cursor, end: range[0], type: 'speech', cut: false });
        }
        segments.push({ start: range[0], end: range[1], type: 'silence', cut: true });
        cursor = range[1];
      });
      if (duration - cursor > 0.001) {
        segments.push({ start: cursor, end: duration, type: 'speech', cut: false });
      }
      if (!segments.length) {
        segments.push({ start: 0, end: duration, type: 'speech', cut: false });
      }
    }

    function detectSilencesLegacy() {
'''

HTML_SELECT = r'''            <span>✂</span> قص الصمت تلقائياً
          </button>
          <select id="detectPresetSelect" class="text-xs bg-[#141d2d] text-gray-300 border border-[#223047] px-2 py-2 rounded-lg" title="نمط القص: يُطبَّق عند الضغط على زرار قص الصمت">
            <option value="reels">ريلز سريع</option>
            <option value="standard">قياسي</option>
            <option value="relaxed">هادئ (شرح/بودكاست)</option>
          </select>
'''

JS_PRESET_LISTENER = r'''    document.getElementById('btnAutoCut').addEventListener('click', autoCutSilences);
    const detectPresetSelect = document.getElementById('detectPresetSelect');
    detectPresetSelect.value = detectionPreset;
    detectPresetSelect.addEventListener('change', function() {
      detectionPreset = Object.prototype.hasOwnProperty.call(DETECTION_PRESETS, this.value)
        ? this.value
        : 'standard';
      localStorage.setItem('auto_silence_detect_preset', detectionPreset);
    });
'''

# ------------------------------------------------------------------ Python

OLD_AUDIO_FILTER = '''                    if has_audio:
                        audio_filters.append(
                            f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
                            f"asetpts=PTS-STARTPTS[a{index}]"
                        )
'''

NEW_AUDIO_FILTER = '''                    if has_audio:
                        # fade قصير على حدود المقطع يمنع "التكّات" عند نقط القص
                        fade_len = min(0.010, (end - start) / 4)
                        audio_filters.append(
                            f"[0:a]atrim=start={start:.6f}:end={end:.6f},"
                            f"asetpts=PTS-STARTPTS,"
                            f"afade=t=in:st=0:d={fade_len:.4f},"
                            f"afade=t=out:st={max(0.0, (end - start) - fade_len):.6f}:d={fade_len:.4f}"
                            f"[a{index}]"
                        )
'''

MERGE_FUNC = '''def merge_contiguous_segments(segments, tolerance=0.002):
    """يدمج المقاطع المتلاصقة (بدون قص بينها) عشان ما يتعملش fade وسط كلام متواصل."""
    merged = []
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        if merged and start - merged[-1]["end"] <= tolerance:
            merged[-1]["end"] = max(merged[-1]["end"], end)
        else:
            merged.append({"start": start, "end": end})
    return merged


class CustomHandler(SimpleHTTPRequestHandler):
'''


# ------------------------------------------------------------------ helpers

def fail(message):
    print(f"[X] {message}")
    print("    لم يتم تعديل أي ملف.")
    sys.exit(1)


def read_text(path):
    if not os.path.isfile(path):
        fail(f"الملف {path} غير موجود. شغّل السكريبت من فولدر المشروع.")
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def write_text(path, text, crlf):
    shutil.copy2(path, path + ".bak3")
    if crlf:
        text = text.replace("\n", "\r\n")
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        fail(f"تعذر العثور على: {label} (موجود {text.count(old)} مرة)")
    print(f"[+] {label}")
    return text.replace(old, new, 1)


def main():
    html, html_crlf = read_text(HTML)
    engine, engine_crlf = read_text(ENGINE)
    app, app_crlf = read_text(APP)

    already = [
        "buildSilenceEnvelope" in html,
        "afade=t=in" in engine,
        "merge_contiguous_segments" in app,
    ]
    if all(already):
        print("[=] التعديلات مطبقة بالفعل.")
        return
    if any(already):
        fail("بعض التعديلات مطبقة وبعضها لا. ارجع للنسخ .bak3 أو راجع الملفات يدوياً.")

    # ---- HTML
    html = replace_once(html, "    let audioPeaks = [];\n", JS_DECLARATIONS, "إعدادات الكشف والأنماط")
    html = replace_once(
        html,
        "        let uploadWarning = '';\n        sourceUploadReady = false;\n",
        JS_ENVELOPE_CALL,
        "حساب مغلّف الصوت عند تحميل الفيديو",
    )
    html = replace_once(
        html,
        "    function detectSilences() {\n      segments = [];\n      if (!duration || !audioPeaks.length) return;\n",
        JS_DETECTION + "      segments = [];\n      if (!duration || !audioPeaks.length) return;\n",
        "كشف السكتات الجديد (والقديم كاحتياطي)",
    )
    html = replace_once(
        html,
        "            <span>✂</span> قص الصمت تلقائياً\n          </button>\n",
        HTML_SELECT,
        "قائمة أنماط القص بجنب الزرار",
    )
    html = replace_once(
        html,
        "    document.getElementById('btnAutoCut').addEventListener('click', autoCutSilences);\n",
        JS_PRESET_LISTENER,
        "ربط قائمة الأنماط",
    )

    # ---- core/editing_engine.py
    engine = replace_once(engine, OLD_AUDIO_FILTER, NEW_AUDIO_FILTER, "fade صوتي على حدود المقاطع")

    # ---- desktop_app.py
    app = replace_once(
        app,
        "class CustomHandler(SimpleHTTPRequestHandler):\n",
        MERGE_FUNC,
        "دالة دمج المقاطع المتلاصقة",
    )
    app = replace_once(
        app,
        "        total_segs = len(kept_segments)\n        if total_segs == 0:\n",
        "        kept_segments = merge_contiguous_segments(kept_segments)\n"
        "        total_segs = len(kept_segments)\n        if total_segs == 0:\n",
        "دمج المقاطع قبل التصدير",
    )

    # تأكد إن ملفات البايثون سليمة قبل الكتابة
    import ast
    for name, code in ((ENGINE, engine), (APP, app)):
        try:
            ast.parse(code)
        except SyntaxError as exc:
            fail(f"{name}: خطأ syntax في الناتج ({exc}).")

    write_text(HTML, html, html_crlf)
    write_text(ENGINE, engine, engine_crlf)
    write_text(APP, app, app_crlf)

    print()
    print("[OK] تم. نسخ احتياطية: *.bak3")
    print("     جرّب: حمّل فيديو، غيّر النمط من القائمة، اضغط 'قص الصمت تلقائياً'،")
    print("     واسمع التصدير عند نقط القص.")


if __name__ == "__main__":
    main()

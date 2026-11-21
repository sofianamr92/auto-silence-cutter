"""
apply_detection_html.py

كشف سكتات أدق في AutoSilenceCutter.html (من فولدر المشروع). ملف واحد بس، من غير أي تعديل في ملفات بايثون.

  - كشف بدقة 10ms بدل 1200 نقطة للفيديو كله
    (الفيديو 10 دقايق كان بيتقسم لقطع 0.5 ثانية، والحدود بتتقرب لأقرب نص ثانية)
  - عتبة dB مع تقدير تلقائي لمستوى الضوضاء (متحفّظ: مش بيرفع العتبة أكتر من 6dB)
  - هوامش طبيعية: بيسيب جزء من الوقفة بعد الكلام وقبل الكلام اللي بعده
    (مش بيقص أول وآخر الكلمة)
  - 3 أنماط بقائمة جنب زرار "قص الصمت تلقائياً": ريلز سريع / قياسي / هادئ
  - لو حساب المغلّف فشل، بيرجع للكشف القديم تلقائياً

التشغيل (من فولدر المشروع):
    python -X utf8 apply_detection_html.py

بيعمل نسخة احتياطية AutoSilenceCutter.html.bak5 ويوقف من غير تعديل لو أي جزء مش موجود.
"""
import os
import shutil
import sys

HTML = "AutoSilenceCutter.html"

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


def fail(message):
    print(f"[X] {message}")
    print("    لم يتم تعديل أي ملف.")
    sys.exit(1)


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        fail(f"تعذر العثور على: {label} (موجود {count} مرة)")
    print(f"[+] {label}")
    return text.replace(old, new, 1)


def main():
    if not os.path.isfile(HTML):
        fail(f"الملف {HTML} غير موجود. شغّل السكريبت من فولدر المشروع.")
    with open(HTML, "rb") as handle:
        raw = handle.read()
    crlf = b"\r\n" in raw
    html = raw.decode("utf-8").replace("\r\n", "\n")

    if "buildSilenceEnvelope" in html:
        print("[=] الكشف الجديد مطبق بالفعل.")
        return

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

    shutil.copy2(HTML, HTML + ".bak5")
    if crlf:
        html = html.replace("\n", "\r\n")
    with open(HTML, "wb") as handle:
        handle.write(html.encode("utf-8"))

    print()
    print("[OK] تم. النسخة الاحتياطية: AutoSilenceCutter.html.bak5")
    print("     أعد تشغيل التطبيق، حمّل فيديو، وجرّب الأنماط التلاتة")
    print("     (غيّر النمط ثم اضغط 'قص الصمت تلقائياً'، وCtrl+Z يرجّع).")


if __name__ == "__main__":
    main()

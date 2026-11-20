const fs = require('fs');
const path = require('path');

const root = process.cwd();
const htmlPath = path.join(root, 'AutoSilenceCutter.html');
const pythonPath = path.join(root, 'desktop_app.py');

function replaceOnce(source, marker, replacement, file) {
  const first = source.indexOf(marker);
  if (first === -1 || source.indexOf(marker, first + marker.length) !== -1) {
    throw new Error(`Expected exactly one insertion point in ${file}: ${marker.trim()}`);
  }
  return source.slice(0, first) + replacement + source.slice(first);
}

function updateHtml(html) {
  const featureExists = html.includes('id="btnRemoveVideo"')
    || html.includes('function removeCurrentVideo');
  if (featureExists) {
    if (
      html.includes('id="btnRemoveVideo"')
      && html.includes('function removeCurrentVideo')
      && html.includes("id: 'file.remove'")
      && html.includes("e.code === 'Delete'")
    ) {
      return html;
    }
    throw new Error('AutoSilenceCutter.html contains an incomplete Remove-Video feature.');
  }

  html = replaceOnce(
    html,
    '      <button id="btnOpenExport"',
    `      <button id="btnRemoveVideo" class="text-xs bg-rose-600 hover:bg-rose-500 text-white font-bold px-3.5 py-1.5 rounded-lg flex items-center gap-1.5 transition shadow-lg shadow-rose-600/20" title="حذف الفيديو (Ctrl+Delete)">
        <span>🗑️</span> حذف الفيديو
      </button>
`,
    'AutoSilenceCutter.html'
  );
  html = replaceOnce(
    html,
    "      { id: 'file.help',",
    "      { id: 'file.remove', name: 'حذف الفيديو الحالي', category: 'File', key: 'Ctrl + Delete', desc: 'إزالة الفيديو الحالي من مساحة العمل مع إبقاء الملف الأصلي' },\n",
    'AutoSilenceCutter.html'
  );
  html = replaceOnce(
    html,
    '    // File Upload',
    `    async function removeCurrentVideo() {
      if (!mainVideo.src || !duration) return;
      if (!confirm('هل تريد إزالة الفيديو الحالي من مساحة العمل؟ سيبقى الملف الأصلي على جهازك.')) return;

      const button = document.getElementById('btnRemoveVideo');
      button.disabled = true;
      try {
        const response = await fetch('/api/remove_video', { method: 'POST' });
        const result = await response.json();
        if (!response.ok || !result.success) {
          throw new Error(result.error || \`HTTP \${response.status}\`);
        }

        mainVideo.pause();
        const objectUrl = mainVideo.dataset.objectUrl;
        mainVideo.removeAttribute('src');
        mainVideo.load();
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        delete mainVideo.dataset.objectUrl;

        duration = 0;
        audioPeaks = [];
        segments = [];
        historyStack = [];
        redoStack = [];
        sourceUploadReady = false;
        originalFileName = 'Video.mp4';
        videoInput.value = '';
        currentTimeDisplay.textContent = '00:00.0';
        totalTimeDisplay.textContent = '00:00.0';

        [
          'statResolution', 'statOrigDuration', 'statOrigSize', 'statNewDuration',
          'statSpeechClips', 'statSilenceCount', 'statCutDuration', 'statRatioText'
        ].forEach(id => {
          const element = document.getElementById(id);
          if (element) element.textContent = '—';
        });

        const progressBar = document.getElementById('statProgressBar');
        if (progressBar) progressBar.style.width = '0%';
        exportModal.classList.add('hidden');
        exportProgressContainer.classList.add('hidden');
        exportProgressBar.style.width = '0%';
        exportPercentText.textContent = '0%';
        exportStatusText.textContent = '';
        emptyState.classList.remove('hidden');
        editorState.classList.add('hidden');
        btnModeCut.click();
      } catch (error) {
        alert(\`تعذر إزالة الفيديو: \${error.message || error}\`);
      } finally {
        button.disabled = false;
      }
    }

    document.getElementById('btnRemoveVideo').addEventListener('click', removeCurrentVideo);
`,
    'AutoSilenceCutter.html'
  );
  html = replaceOnce(
    html,
    "      if (norm === 'Ctrl+s') {",
    `      if (e.ctrlKey && e.code === 'Delete') {
        e.preventDefault();
        removeCurrentVideo();
        return;
      }
`,
    'AutoSilenceCutter.html'
  );
  return html;
}

function updatePython(python) {
  if (python.includes("path == '/api/remove_video':")) return python;

  return replaceOnce(
    python,
    "        if path == '/api/select_video_native':",
    `        if path == '/api/remove_video':
            global UPLOADED_SOURCE_PATH
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

        elif path == '/api/select_video_native':`,
    'desktop_app.py'
  );
}

function main() {
  if (!fs.existsSync(htmlPath) || !fs.existsSync(pythonPath)) {
    throw new Error('Run this task from the workspace root containing AutoSilenceCutter.html and desktop_app.py.');
  }

  const html = updateHtml(fs.readFileSync(htmlPath, 'utf8'));
  const python = updatePython(fs.readFileSync(pythonPath, 'utf8'));
  fs.writeFileSync(htmlPath, html, 'utf8');
  fs.writeFileSync(pythonPath, python, 'utf8');
  console.log('Remove-Video feature is installed.');
}

try {
  main();
} catch (error) {
  console.error(`Could not install Remove-Video feature: ${error.message}`);
  process.exitCode = 1;
}

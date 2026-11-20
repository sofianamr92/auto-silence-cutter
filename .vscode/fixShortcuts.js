const fs = require('fs');
const path = require('path');

const htmlPath = path.join(process.cwd(), 'AutoSilenceCutter.html');
const verticalShortcut = "      { id: 'layout.toggleVertical', name: 'الوضع الرأسي', category: 'Workspace', key: 'Ctrl + Shift + V', desc: 'الانتقال مباشرة إلى تخطيط مساحة العمل الرأسي' },\n";
const verticalHandler = `      if (norm === 'Ctrl+Shift+v') {
        e.preventDefault();
        setWorkspaceLayout('vertical');
        return;
      }
`;
const cycleHandler = `      if (norm === 'Ctrl+l') {
        e.preventDefault();
        const layoutOptions = [...document.querySelectorAll('.layout-opt')];
        const currentIndex = layoutOptions.findIndex(option => !option.querySelector('.check-icon').classList.contains('hidden'));
        const nextLayout = layoutOptions[(currentIndex + 1) % layoutOptions.length];
        if (nextLayout) setWorkspaceLayout(nextLayout.dataset.layout);
        return;
      }
`;
const legacyCycleHandler = `      } else if (norm === 'Ctrl+l') {
        e.preventDefault();
        let layoutOptions = [...document.querySelectorAll('.layout-opt')];
        let currentIndex = layoutOptions.findIndex(option => !option.querySelector('.check-icon').classList.contains('hidden'));
        let nextLayout = layoutOptions[(currentIndex + 1) % layoutOptions.length];
        if (nextLayout) setWorkspaceLayout(nextLayout.dataset.layout);
      }
`;

function insertOnce(source, marker, addition, description) {
  const first = source.indexOf(marker);
  if (first === -1 || source.indexOf(marker, first + marker.length) !== -1) {
    throw new Error(`Expected one ${description} insertion point.`);
  }
  return source.slice(0, first) + addition + source.slice(first);
}

function replaceOnce(source, before, after, description) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) throw new Error(`Expected one ${description} to update; found ${count}.`);
  return source.replace(before, after);
}

function updateShortcuts(html) {
  const hasEntry = html.includes("id: 'layout.toggleVertical'");
  const hasHandler = html.includes("setWorkspaceLayout('vertical');\n        return;");
  if (hasEntry !== hasHandler) {
    throw new Error('The vertical shortcut entry and handler are out of sync.');
  }

  if (!hasEntry) {
    html = insertOnce(
      html,
      "      { id: 'file.help',",
      verticalShortcut,
      'shortcut list'
    );
    html = insertOnce(
      html,
      "      if (!mainVideo.src) return;",
      verticalHandler,
      'keyboard handler'
    );
  }

  const legacyCount = html.split(legacyCycleHandler).length - 1;
  if (legacyCount > 1) throw new Error('Found multiple legacy Ctrl+L handlers.');
  if (legacyCount === 1) html = html.replace(legacyCycleHandler, '      }\n');

  if (!html.includes("if (norm === 'Ctrl+l') {\n        e.preventDefault();\n        const layoutOptions")) {
    html = insertOnce(
      html,
      "      if (!mainVideo.src) return;",
      cycleHandler,
      'workspace cycle handler'
    );
  }

  html = replaceOnce(
    html,
    "      { id: 'ws.toggle', name: 'تبديل شكل مساحة العمل', category: 'Workspace', key: 'Ctrl + L', desc: 'التبديل الفوري بين الأوضاع' },",
    "      { id: 'ws.toggle', name: 'تبديل شكل مساحة العمل', category: 'Workspace', key: 'Ctrl + L', desc: 'التبديل بين الوضعين الأفقي والرأسي' },",
    'workspace shortcut description'
  );
  return html;
}

try {
  if (!fs.existsSync(htmlPath)) {
    throw new Error(`AutoSilenceCutter.html not found: ${htmlPath}`);
  }
  const original = fs.readFileSync(htmlPath, 'utf8');
  const lineEnding = original.includes('\r\n') ? '\r\n' : '\n';
  const updated = updateShortcuts(original.replace(/\r\n/g, '\n')).replace(/\n/g, lineEnding);
  if (updated !== original) fs.writeFileSync(htmlPath, updated, 'utf8');
  console.log('Keyboard shortcuts updated for the existing AutoSilenceCutter.html app.');
} catch (error) {
  console.error(`Could not update keyboard shortcuts: ${error.message}`);
  process.exitCode = 1;
}

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(process.cwd(), 'AutoSilenceCutter.html');
const removedLayouts = ['timeline-focus', 'preview-focus', 'compact'];

function replaceExact(source, before, after, description) {
  const count = source.split(before).length - 1;
  if (count === 0 && source.includes(after)) return source;
  if (count !== 1) {
    throw new Error(`Expected one ${description} to replace; found ${count}.`);
  }
  return source.replace(before, after);
}

function removeCssRules(html, className) {
  const expression = new RegExp(
    `^[\\t ]*\\.${className}\\b[^\\{]*\\{[^}]*\\}[\\t ]*\\r?\\n?`,
    'gm'
  );
  return html.replace(expression, '');
}

function removeLayoutButton(html, layout) {
  const expression = new RegExp(
    `^[\\t ]*<button(?=[^>]*\\bdata-layout="${layout}")[^>]*>[\\s\\S]*?<\\/button>[\\t ]*\\r?\\n?`,
    'gm'
  );
  return html.replace(expression, '');
}

function updateHtml(html) {
  for (const layout of [...removedLayouts, 'vertical']) {
    html = removeCssRules(html, `layout-${layout}`);
  }

  html = replaceExact(
    html,
    '    /* أوضاع مساحة العمل (Workspace Layout Modes) */',
    '    /* تخطيط مساحة العمل الرأسي */',
    'layout CSS comment'
  );

  const verticalStyles = `    .layout-vertical #playerWrapper {
      width: min(100%, 236.25px, 39.375vh) !important;
      height: min(420px, 70vh) !important;
      max-height: none !important;
      margin: 0 auto;
    }
    .layout-vertical #mainVideo {
      width: 100%;
      height: 100%;
      max-height: none !important;
      object-fit: contain;
    }
`;
  if (!html.includes('.layout-vertical #playerWrapper')) {
    html = replaceExact(html, '  </style>', `${verticalStyles}  </style>`, 'style closing tag');
  }

  for (const layout of removedLayouts) {
    html = removeLayoutButton(html, layout);
  }

  const oldLayoutData = `      const layoutData = {
        'horizontal': { icon: '💻', label: 'أفقي قياسي' },
        'vertical': { icon: '📱', label: 'رأسي (9:16)' },
        'timeline-focus': { icon: '🎚️', label: 'تركيز التايم لاين' },
        'preview-focus': { icon: '🖥️', label: 'تركيز المعاينة' },
        'compact': { icon: '📐', label: 'مساحة مضغوطة' }
      };`;
  const newLayoutData = `      const layoutData = {
        'horizontal': { icon: '💻', label: 'أفقي قياسي' },
        'vertical': { icon: '📱', label: 'رأسي (9:16)' }
      };`;
  html = replaceExact(html, oldLayoutData, newLayoutData, 'layout options map');

  html = replaceExact(
    html,
    "    function setWorkspaceLayout(layoutName) {\n      let app = document.getElementById('editorState');",
    "    function setWorkspaceLayout(layoutName) {\n      if (!['horizontal', 'vertical'].includes(layoutName)) layoutName = 'horizontal';\n      let app = document.getElementById('editorState');",
    'layout validation'
  );

  html = replaceExact(
    html,
    "      app.classList.remove('layout-horizontal', 'layout-vertical', 'layout-timeline-focus', 'layout-preview-focus', 'layout-compact');",
    "      app.classList.remove('layout-horizontal', 'layout-vertical');",
    'layout class list'
  );

  for (const layout of removedLayouts) {
    if (html.includes(`data-layout="${layout}"`) || html.includes(`'${layout}':`)) {
      throw new Error(`The removed layout "${layout}" is still referenced.`);
    }
  }
  if (!html.includes('data-layout="horizontal"') || !html.includes('data-layout="vertical"')) {
    throw new Error('Both horizontal and vertical layout options must remain.');
  }

  return html;
}

try {
  if (!fs.existsSync(htmlPath)) {
    throw new Error(`AutoSilenceCutter.html not found: ${htmlPath}`);
  }
  const original = fs.readFileSync(htmlPath, 'utf8');
  const lineEnding = original.includes('\r\n') ? '\r\n' : '\n';
  const updated = updateHtml(original.replace(/\r\n/g, '\n')).replace(/\n/g, lineEnding);
  if (updated !== original) fs.writeFileSync(htmlPath, updated, 'utf8');
  console.log('Only horizontal and vertical workspace layouts are enabled.');
} catch (error) {
  console.error(`Could not trim workspace layouts: ${error.message}`);
  process.exitCode = 1;
}

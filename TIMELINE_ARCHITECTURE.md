# PySide6 Timeline architecture

## Goal and scope

This design targets the native PySide6 application launched by [app/main.py](./app/main.py). It keeps the browser editor in [AutoSilenceCutter.html](./AutoSilenceCutter.html) and its `desktop_app.py` server intact; those currently use a separate project schema and export flow. Interoperability between the two editors should be an explicit import/export adapter, not implicit shared mutable state.

The native timeline will evolve around one canonical `TimelineDocument`. The scene, player, autosave, undo history, and export are consumers or controlled mutators of that document—not independent edit models. Existing import, silence/breath analysis, C/Q/W shortcuts, FFmpeg video export, CapCut package, and EDL/JSON output remain supported.

The current painted widget is too limited for clip selection, trim handles, multiple lanes, and scrolling. Use Qt Graphics View for interactive clip geometry, while keeping playback controls and the application shell in their existing widgets.

## Proposed layers

```text
MainWindow
  ├─ TimelineView / TimelineScene ── reads document; forwards user intent
  ├─ TimelineController ──────────── validates and applies edit commands
  ├─ TimelinePlaybackController ──── maps playhead <-> source PTS; drives QMediaPlayer
  ├─ TimelineDocument ────────────── canonical project/edit data
  └─ TimelineExportAdapter ───────── immutable export snapshot -> FFmpeg ranges
```

### 1. Timeline document and media model

Introduce domain models in `timeline/document.py` (or an equivalent single model module):

- `MediaSource`: stable media ID, path/reference, probe metadata, and optional cached frame index.
- `TimelineTrack`: stable ID, kind (`video`/`audio`), order, enabled/locked state, and clips.
- `ClipModel`: stable clip ID, media/track IDs, source in/out, timeline start, rational playback rate, enabled flags, origin, and optional linked-group ID.
- `TimelineDocument`: tracks, media sources, duration, analysis markers, selected clip IDs, playhead, zoom/scroll state, workspace state, and a monotonically increasing revision.

Store source/timeline times as non-negative integer microseconds. Use rational values for frame rates and playback rates. Validate clip bounds and track membership at the document/controller boundary. Keep `MediaInfo`, `Clip`, and `ProjectState` as compatibility imports/properties during migration so existing consumers can be moved in small, testable steps; seconds should be converted only at UI, player, and FFmpeg boundaries.

Analysis regions remain tied to source media and are not an alternate edit/export list. The controller converts accepted detections into clip edits; export always derives from document clips. The view must distinguish source-time markers from edited timeline positions rather than drawing both in one coordinate domain without conversion.

### 2. Timeline controller and commands

Add a `TimelineController` as the only normal edit entry point. It handles selection, split, trim, move, delete, track enable/disable, marker actions, and snapping. Keep the existing command/undo pattern, but make commands capture IDs and exact before/after model values rather than loose list indexes or floats. Commands update the document revision and emit a document-changed signal; the view and export UI react to that signal.

Preserve C/Q/W behavior. C splits the selected/containing clip at the frame-resolved playhead; Q and W trim its start/end. Add mouse interactions as commands so dragging and keyboard edits share validation, snapping, undo, and export semantics.

### 3. Timebase and coordinate mapping

Add a `FrameIndexMapper` backed by actual frame presentation timestamps from FFprobe for variable-frame-rate media. Build the index off the UI thread, cache it using a media fingerprint, and expose previous/next/nearest-frame operations. For constant-frame-rate sources, retain the exact rational rate; do not infer VFR frame positions from a single float FPS value.

Add a `TimelineCoordinateMapper` that converts integer timeline time to scene coordinates and back. Zoom changes should preserve the time under the pointer/playhead; horizontal scrolling should reveal the complete document. Display frame timecode only when a reliable frame rate/index is available; otherwise display milliseconds.

### 4. Timeline rendering and interaction

Use the existing `timeline/` package for the view layer:

- `TimelineScene` owns derived graphics items and refreshes them from document revisions.
- `TimelineView` owns zoom, scroll, keyboard focus, and viewport-to-time conversion.
- `TimelineClipItem` renders a real clip body, label, duration, selection, and trim handles; drag/trim emits controller commands.
- `TimelinePlayheadItem` displays and drags the authoritative timeline playhead.
- Track headers/ruler remain fixed relative to the viewport; video and linked audio lanes scroll together.
- Detection markers are shown in a clearly identified source-analysis lane, mapped through the document when shown against edited content.

Use Qt's viewport culling and redraw only visible items. Do not generate thumbnails or decode waveforms from paint handlers.

### 5. Waveform and thumbnail services

Generate waveform peak pyramids and video thumbnails asynchronously through FFmpeg/FFprobe. Cache artifacts outside the source media, keyed by media fingerprint and extraction parameters. Provide results to scene items on the GUI thread; missing cache data is a normal loading state, while extraction failures are surfaced to the user/log rather than silently represented as successful data.

Waveform samples shown for a clip must be selected from that clip's source in/out range and drawn at its timeline position. Thumbnails similarly sample the clip's source range. Editing a clip changes the visible mapping, not the source file or cached media.

### 6. Playback synchronization

Add `TimelinePlaybackController` between the document, `PlayerWidget`, and `QMediaPlayer`:

- A timeline seek maps the edited playhead through enabled clips to the correct source media and PTS.
- Player position changes map back through the same clip mapping and update the document playhead and scene.
- Playback skips removed gaps/disabled clips and stops at the edited document duration.
- Frame-step buttons use `FrameIndexMapper`, and the player time label uses the source's supported timebase.

This adapter is required before visual timeline positions can be considered authoritative; direct `timeline_seconds * 1000` seeking is not retained for edited playback.

### 7. Export adapter

Create `TimelineExportAdapter` to read one validated document revision and produce an immutable snapshot. The adapter sorts enabled clips by timeline order, converts microseconds/PTS to renderer inputs, and preserves linked audio/video behavior. The export worker receives only this snapshot and never reads a live document.

Keep `FFmpegRenderer` as the rendering backend initially. Route final video, CapCut package, and EDL/JSON through the same snapshot. For the existing single-source sequential workflow, the adapter should produce the same source ranges as current exports; tests must prove that split/trim/undo alter preview mapping and export ranges consistently.

### 8. Persistence and workspace

Add a versioned native project serializer that stores the document, analysis markers, media references, playhead, zoom/scroll, and workspace layout—not copied source videos. Use a native file format/schema that cannot be mistaken for the browser editor's existing `.silenceproject` payload. Missing media should be reported and relinkable. Keep browser IndexedDB/Supabase saves unchanged until a deliberate conversion adapter is implemented.

Wire the current workspace menu choices to actual splitter/view arrangements and persist the chosen layout through the native project serializer. Preserve sensible defaults when loading older or incomplete project data.

## Migration and delivery order

1. **Model foundation:** add integer-time models, validation, compatibility accessors, and model/timebase tests. Keep current rendering/export behavior available.
2. **Controller and commands:** route import-derived clips and C/Q/W through the document/controller; make undo/redo operate on stable IDs and exact states.
3. **Playback and export parity:** introduce frame/time mapping, bidirectional playhead synchronization, and immutable export snapshots. Test source ranges and timeline positions across split, trim, and removed regions.
4. **Interactive view:** replace the minimal painter widget with the Graphics View scene, real clip items, tracks, ruler, scrolling, selection, snapping, and handles. Retain the current dark styling and keyboard shortcuts.
5. **Media visuals and project state:** add cached asynchronous waveform/thumbnails, versioned native project save/load, and complete workspace behavior.
6. **Compatibility and packaging:** update `setup_all.py` templates for new/changed native modules, keep `AutoSilenceCutter.spec` building `app/main.py`, and verify the README's browser workflow remains unchanged.

## Acceptance checks

- Every visible clip, marker, waveform, and playhead is derived from the current document revision.
- Split, trim, move, delete, undo, and redo update the same document consumed by playback and export.
- Timeline-to-source and source-to-timeline mapping is tested for gaps, clip boundaries, and VFR timestamps.
- Zoom/scroll can reach the entire edit, and selection/trim handles are operable with mouse and keyboard.
- Reopening a native project restores edits and UI state while reporting missing source media.
- Existing browser app behavior and its cloud project format are not changed by the native migration.
- Targeted tests, full Python tests, syntax checks, and a PyInstaller build cover the delivered changes.

# Timeline audit

## Scope and application entry points

The repository currently contains two separate editing applications:

- The native PySide6 application starts at [app/main.py](./app/main.py), creates [ui/main_window.py](./ui/main_window.py), and is the entry point referenced by [AutoSilenceCutter.spec](./AutoSilenceCutter.spec).
- The documented `run_app.bat` / README workflow starts [desktop_app.py](./desktop_app.py), which serves [AutoSilenceCutter.html](./AutoSilenceCutter.html) in a browser. It has its own JavaScript timeline, project format, preview, and export requests.

The requested PySide6 Timeline work belongs to the native application. The browser editor is not wired to the native `ProjectState`; changing one does not update the other.

## Native PySide6 timeline

### Files and responsibilities

- [ui/main_window.py](./ui/main_window.py) owns the native window, import and analysis lifecycle, the `ProjectState`, keyboard shortcuts, the undo stack, and links between the player, timeline, and export dialog.
- [timeline/timeline_widget.py](./timeline/timeline_widget.py) is a custom `QWidget`/`QPainter` view. It draws a ruler, one track background, approved detection regions, waveform peaks, clip-start lines, and the playhead. It also handles click-and-drag playhead seeking and Ctrl+wheel zoom.
- [core/editing_engine.py](./core/editing_engine.py) defines `MediaInfo`, `DetectedRegion`, `Clip`, `ProjectState`, and `FFmpegRenderer`.
- [editing/commands.py](./editing/commands.py) implements the C split and Q/W trim commands. [editing/undo_redo.py](./editing/undo_redo.py) stores up to 50 commands.
- [ui/player_widget.py](./ui/player_widget.py) wraps `QMediaPlayer` and displays a timecode. [core/media_probe.py](./core/media_probe.py) probes source metadata.
- [core/silence_detection.py](./core/silence_detection.py) creates silence regions; [core/breath_detection.py](./core/breath_detection.py) adds breath regions.
- [export/export_dialog.py](./export/export_dialog.py) and [export/capcut_package.py](./export/capcut_package.py) turn kept source ranges into FFmpeg output, CapCut package files, or EDL/JSON.

### Current data and event flow

1. Import probes the media and creates one full-length `Clip`.
2. `AnalysisWorker` detects silence and breaths. `on_analysis_finished` merges approved regions, replaces the clip list with the remaining source ranges, and recalculates each clip's `timeline_in`.
3. The same completion handler reduces decoded mono audio into at most 1,200 peak values and passes them directly to `TimelineWidget`; those peaks are not part of `ProjectState` or a persistent cache.
4. The timeline click/drag writes `ProjectState.current_playhead` and seeks the player to the same numeric time in milliseconds.
5. C/Q/W commands modify `ProjectState.clips`; undo/redo restore those command mutations. The window manually requests a repaint after each operation.
6. Video and CapCut export enumerate the non-cut clips and pass their `source_in`/`source_out` ranges to `FFmpegRenderer`. EDL/JSON uses the same clip list.

### Data model and timing

- `ProjectState` is the mutable edit state used by the native UI and commands. It contains media metadata, clips, detections, a float-seconds playhead, zoom level, and active layout.
- A `Clip` has an ID, source path, floating-point source in/out and timeline start, and an `is_cut` flag. It has no media ID, track ID, playback-rate, linked-audio group, or selection state.
- `DetectedRegion` uses floating-point source seconds. `MediaInfo.fps` is a float derived from FFprobe's `r_frame_rate`; frame PTS/index data and VFR mapping are not collected.
- The player formats frames at a fixed 30 fps. The existing timecode formatter rounds FPS to a nominal integer rate.
- There is no native project save/load or timeline serialization path. The `projects` and `workspace` packages do not provide one.

## Current gaps and integration conflicts

### Playback and coordinate domains

- `ProjectState` clips use both source time (`source_in`/`source_out`) and edited timeline time (`timeline_in`), but the native playhead is sent directly to `QMediaPlayer` as source time. There is no timeline-to-source mapping for playback across removed regions.
- The timeline ruler and playhead span source-media duration; detected regions are positioned in source time, while clip markers use edited `timeline_in`. After cuts, these overlays can refer to different time domains on the same axis.
- `QMediaPlayer.positionChanged` updates only the player label. It does not update the timeline playhead or `ProjectState.current_playhead`; the declared frame-step signals are not connected in the main window.
- Consequently, playback, displayed playhead, edit commands, and exported source ranges are not guaranteed to describe the same edit position.

### View and editing capabilities

- The native view is one painted lane, not separate video/audio tracks. Clips are represented by start lines rather than selectable clip items.
- There is no clip selection, drag/move, trim handle, delete operation, snapping, marker editing, thumbnail strip, or vertical track/header layout.
- Zoom changes the pixel scale but the widget has no horizontal scroll area or scrollbar; content beyond its viewport is clipped. `TimelineWidget.zoom_factor` is separate from `ProjectState.zoom_level` and is not saved.
- The waveform is a simple peak envelope with no cache or asynchronous thumbnail/waveform service. Painting iterates over visible pixels and maps them to peaks on each repaint.
- The detected regions are hover tooltips/overlays only; the native view has no interaction to restore or retoggle a region after detection.
- C/Q/W use float seconds and a fixed 0.05-second trim threshold, rather than frame-aware boundaries. Commands are the only tested native timeline behavior; there are no widget, playback-sync, or export-integration tests.

### Export, persistence, and workspace

- Export reads the mutable `ProjectState` from a worker thread instead of receiving an immutable edit snapshot. The GUI does not establish a model-level freeze while rendering.
- The renderer accepts source ranges in seconds. There is no dedicated adapter validating that preview and export use the same timeline mapping.
- Native projects are not saved, so clips, analysis, playhead, selection, zoom, and layout cannot be restored by the PySide6 application.
- The workspace menu advertises several layouts, but the native handler only distinguishes Timeline Focus and Preview Focus; other choices use the same splitter sizing. It changes player/timeline height only.

## Separate browser editor

- The browser editor keeps a separate `segments` array in JavaScript. Its canvas draws that array and its waveform; click/Shift-click, C/Q/W, undo/redo, and export requests also operate on it.
- The browser preview uses `HTMLVideoElement.currentTime`, and smart-cut preview skips segments marked `cut`. Export posts kept source ranges to `/api/export_video` or `/api/export_cuts` in [desktop_app.py](./desktop_app.py).
- Browser projects are saved in IndexedDB and can sync through the existing Supabase services. This persistence format is not the native `ProjectState` format.
- This implementation has a horizontally scrollable canvas and 1x/2x/4x zoom, but still does not model independent tracks or clip objects with editing handles.

## Generator and source-of-truth note

[setup_all.py](./setup_all.py) embeds templates for the native model, commands, and timeline widget. It skips files that already exist, but a fresh generated project would use those embedded templates; the embedded timeline template is not identical to the current runtime file. Any new native timeline modules or intentional template changes need corresponding generator updates to keep generated installations consistent.

## Summary

The native editor already has useful foundations—source-range clips, detection results, command-based split/trim, undo/redo, a player, and FFmpeg export—but the current `TimelineWidget` is a minimal overview rather than an editable multi-track timeline. The primary redesign requirement is to establish one canonical timeline-time model and route playback, editing, rendering, persistence, and export through it. The browser editor should remain a separate compatibility surface unless a later, explicit migration is planned.

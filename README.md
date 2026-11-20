# Auto Silence Cutter

Local video editor for detecting silence and cutting clips. See [README_AR.md](README_AR.md)
for setup and feature details.

## Requirements

- Python dependencies: `python -m pip install -r requirements.txt`
- FFmpeg and FFprobe installed and available on `PATH` (`ffmpeg -version`,
  `ffprobe -version`)
- Development/build dependencies: `python -m pip install -r requirements-dev.txt`

## Run the desktop editor

On Windows, install FFmpeg and FFprobe on `PATH`, then run:

```powershell
python -m app.main
```

The PySide6 editor saves projects as `.asctimeline`. See [README_AR.md](README_AR.md)
for the editor and browser-app setup details.
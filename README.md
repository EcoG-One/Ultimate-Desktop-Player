
# EcoG Qt Desktop Player (PySide6)

Python / Qt (PySide6) desktop replacement for my Web player.

## Features
- Scan folders for audio files
- Search by artist/title/album
- Save/load playlists
- Dual-player crossfade (configurable)
- Experimental "Gap Killer" using QAudioProbe
- Basic metadata with `mutagen`

## Run
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m ecogqt
```

## Package
Use PyInstaller:
```bash
pip install pyinstaller
pyinstaller -n EcoGQtPlayer -w -F ecogqt/__main__.py
```

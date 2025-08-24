
import os
import sys
import json
import math
from pathlib import Path
from dataclasses import dataclass, asdict
from pydub import AudioSegment, silence
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl, QTimer, Property
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QListWidget, QListWidgetItem, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QSplitter, QSlider, QMessageBox, QStyle
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput


try:
    from mutagen import File as MutagenFile
except Exception:
    MutagenFile = None

APP_DIR = Path.home() / ".ecog_qt_player"
APP_DIR.mkdir(exist_ok=True)
PLAYLISTS_FILE = APP_DIR / "playlists.json"
SETTINGS_FILE = APP_DIR / "settings.json"

AUDIO_EXTS = {".mp3",".flac",".m4a",".aac",".ogg",".wav",".wma",".opus"}

def human_time(seconds:int)->str:
    seconds = max(0, int(seconds))
    return f"{seconds//60}:{seconds%60:02d}"

@dataclass
class Track:
    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    cover_path: str = ""
    silences: list = None  # [(start_ms, end_ms), ...]

def read_metadata(p: Path) -> Track:
    t = Track(path=str(p), title=p.stem)
    if MutagenFile is None:
        return t
    try:
        mf = MutagenFile(str(p))
        if not mf:
            return t
        info = mf
        def first(tag):
            v = info.tags.get(tag) if hasattr(info, "tags") and info.tags else None
            if isinstance(v, list) and v:
                return str(v[0])
            if v is not None:
                return str(v)
            return None
        t.title = first("TIT2") or first("TITLE") or t.title
        t.artist = first("TPE1") or first("ARTIST") or ""
        t.album  = first("TALB") or first("ALBUM") or ""
        t.duration = float(getattr(mf.info, "length", 0.0) or 0.0)
    except Exception:
        pass
    return t


def analyze_silences(path, threshold_db=-46, min_silence_ms=500):
    """Use pydub to detect silence in audio file, return [(start,end),...] in ms."""
    try:
        audio = AudioSegment.from_file(path)
        silences = silence.detect_silence(
            audio,
            min_silence_len=min_silence_ms,
            silence_thresh=threshold_db
        )
        return silences
    except Exception:
        return []


class VolumeAnim(QtCore.QObject):
    """Animate QAudioOutput volume 0..1 with QPropertyAnimation."""
    def __init__(self, audio: QAudioOutput, parent=None):
        super().__init__(parent)
        self._audio = audio
    def getVolume(self):
        return self._audio.volume()
    def setVolume(self, v: float):
        self._audio.setVolume(float(v))
    volume = Property(float, getVolume, setVolume)

class PlayerWidget(QWidget):
    request_reveal = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player1 = QMediaPlayer(self)
        self.player2 = QMediaPlayer(self)
        self.audio1 = QAudioOutput(self)
        self.audio2 = QAudioOutput(self)
        self.player1.setAudioOutput(self.audio1)
        self.player2.setAudioOutput(self.audio2)
        self.audio1.setVolume(1.0)
        self.audio2.setVolume(0.0)

        self.current_index = 0
        self.playlist: list[Track] = []
        self.active = 1
        self.crossfade_seconds = 4
        self.gap_enabled = True
        self.silence_threshold_db = -46
        self.silence_min_duration = 0.5
        self._silence_ms = 0

        # UI
        top = QHBoxLayout()
        self.btn_prev = QPushButton(self.style().standardIcon(QStyle.SP_MediaSkipBackward), "")
        self.btn_play = QPushButton(self.style().standardIcon(QStyle.SP_MediaPlay), "")
        self.btn_next = QPushButton(self.style().standardIcon(QStyle.SP_MediaSkipForward), "")
        self.time_label = QLabel("0:00 / 0:00")
        top.addWidget(self.btn_prev); top.addWidget(self.btn_play); top.addWidget(self.btn_next)
        top.addWidget(self.time_label, 1)

        settings = QHBoxLayout()
        self.fade_label = QLabel("Crossfade: 4s")
        self.fade_slider = QSlider(Qt.Horizontal); self.fade_slider.setRange(0, 12); self.fade_slider.setValue(6)
        settings.addWidget(self.fade_label); settings.addWidget(self.fade_slider,1)

        gapbox = QHBoxLayout()
        self.chk_gap = QtWidgets.QCheckBox("Gap Killer")
        self.chk_gap.setChecked(True)
        self.silence_db = QSlider(Qt.Horizontal); self.silence_db.setRange(-60, -20); self.silence_db.setValue(-46)
        self.silence_dur = QSlider(Qt.Horizontal); self.silence_dur.setRange(1, 50); self.silence_dur.setValue(5)
        self.gap_status = QLabel("Monitoring")
        gapbox.addWidget(self.chk_gap); gapbox.addWidget(QLabel("Threshold (dB):")); gapbox.addWidget(self.silence_db,1)
        gapbox.addWidget(QLabel("Min Silence (x100ms):")); gapbox.addWidget(self.silence_dur,1); gapbox.addWidget(self.gap_status)

        meta = QVBoxLayout()
        self.meta_label = QLabel("—")
        self.reveal_btn = QPushButton("Reveal in Folder")
        meta.addWidget(self.meta_label); meta.addWidget(self.reveal_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(settings)
        lay.addLayout(gapbox)
        lay.addLayout(meta)

        # Connections
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_prev.clicked.connect(self.prev_track)
        self.btn_next.clicked.connect(self.next_track)
        self.fade_slider.valueChanged.connect(self.on_fade_changed)
        self.chk_gap.toggled.connect(lambda v: setattr(self, "gap_enabled", v))
        self.silence_db.valueChanged.connect(lambda v: setattr(self, "silence_threshold_db", v))
        self.silence_dur.valueChanged.connect(lambda v: setattr(self, "silence_min_duration", v/10.0))
        self.reveal_btn.clicked.connect(self.reveal_current)

        self.timer = QTimer(self); self.timer.timeout.connect(self.update_time); self.timer.start(250)

        self.player1.mediaStatusChanged.connect(self.on_status1)
        self.player2.mediaStatusChanged.connect(self.on_status2)
        self.player1.positionChanged.connect(self.on_time_change)
        self.player2.positionChanged.connect(self.on_time_change)

    def reveal_current(self):
        if 0 <= self.current_index < len(self.playlist):
            self.request_reveal.emit(self.playlist[self.current_index].path)

    # --- Playback control
    def set_playlist(self, tracks: list[Track], start_index=0):
        self.playlist = tracks
        self.current_index = max(0, min(start_index, len(tracks)-1))
        self.active = 1
        self.audio1.setVolume(1.0); self.audio2.setVolume(0.0)
        self.load_current()
        self.update_meta()

    def on_fade_changed(self, v):
        self.crossfade_seconds = int(v)
        self.fade_label.setText(f"Crossfade: {v}s")

    def toggle_play(self):
        p = self.current_player()
        if p.playbackState() == QMediaPlayer.PlayingState:
            p.pause()
        else:
            p.play()

    def prev_track(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.swap_and_play(load_only=True)
            self.play()

    def next_track(self):
        if self.current_index + 1 < len(self.playlist):
            self.start_crossfade_or_next(force_next=True)

    def load_current(self):
        if not self.playlist:
            return
        url = QUrl.fromLocalFile(self.playlist[self.current_index].path)
        self.current_player().setSource(url)

    def play(self):
        self.current_player().play()

    def current_player(self) -> QMediaPlayer:
        return self.player1 if self.active == 1 else self.player2

    def other_player(self) -> QMediaPlayer:
        return self.player2 if self.active == 1 else self.player1

    def current_audio(self) -> QAudioOutput:
        return self.audio1 if self.active == 1 else self.audio2

    def other_audio(self) -> QAudioOutput:
        return self.audio2 if self.active == 1 else self.audio1

    def on_status1(self, st): self.on_status_generic(self.player1)
    def on_status2(self, st): self.on_status_generic(self.player2)
    def on_status_generic(self, p: QMediaPlayer):
        if p.mediaStatus() == QMediaPlayer.EndOfMedia:
            self.finish_and_advance()

    def on_time_change(self, pos:int):
        self.update_time()
        dur = self.current_player().duration()
        # Silence skip
        if 0 <= self.current_index < len(self.playlist):
            track = self.playlist[self.current_index]
            if track.silences:
                for start, end in track.silences:
                    if start <= pos <= end:
                        self.current_player().setPosition(end + 50)
                        break
        # Crossfade
        if dur > 0 and self.crossfade_seconds > 0:
            ms_left = dur - self.current_player().position()
            if 0 < ms_left <= self.crossfade_seconds * 1000 and self.current_index + 1 < len(self.playlist):
                self.start_crossfade_or_next()

    def start_crossfade_or_next(self, force_next=False):
        if self.other_player().source().isEmpty() or force_next:
            next_idx = self.current_index + 1
            if next_idx >= len(self.playlist): return
            next_url = QUrl.fromLocalFile(self.playlist[next_idx].path)
            self.other_player().setSource(next_url)
        self.other_player().play()
        self.crossfade(self.crossfade_seconds)

    def crossfade(self, seconds:int):
        seconds = max(0, int(seconds))
        if seconds == 0:
            self.finish_and_advance()
            return
        self.fade_out = QtCore.QPropertyAnimation(VolumeAnim(self.current_audio()), b"volume")
        self.fade_in  = QtCore.QPropertyAnimation(VolumeAnim(self.other_audio()), b"volume")
        for anim in (self.fade_out, self.fade_in):
            anim.setDuration(seconds*1000)
            anim.setStartValue(1.0 if anim is self.fade_out else 0.0)
            anim.setEndValue(0.0 if anim is self.fade_out else 1.0)
        self.fade_in.finished.connect(self.finish_and_advance)
        self.fade_out.start(); self.fade_in.start()

    def finish_and_advance(self):
        self.current_player().stop()
        self.active = 2 if self.active == 1 else 1
        self.current_index += 1
        if self.current_index >= len(self.playlist):
            self.current_index = len(self.playlist)-1
            return
        self.other_player().stop()
        self.load_current()
        self.current_player().play()
        self.update_meta()

    def swap_and_play(self, load_only=False):
        self.current_player().stop()
        self.other_player().stop()
        self.active = 2 if self.active == 1 else 1
        self.load_current()
        if not load_only:
            self.current_player().play()
        self.update_meta()

    def update_time(self):
        p = self.current_player()
        pos = p.position()//1000
        dur = max(0, p.duration()//1000)
        self.time_label.setText(f"{human_time(pos)} / {human_time(dur)}")

    def update_meta(self):
        if 0 <= self.current_index < len(self.playlist):
            t = self.playlist[self.current_index]
            self.meta_label.setText(f"<b>{t.artist or 'Unknown Artist'}</b> — {t.title}<br/><span style='color:#666'>{t.album}</span><br/>{t.path}")

    # --- Gap Killer (experimental)
    def on_buffer1(self, buf): self._probe_buffer(buf, which=1)
    def on_buffer2(self, buf): self._probe_buffer(buf, which=2)

    def _probe_buffer(self, buffer, which:int):
        if not self.gap_enabled:
            self._silence_ms = 0
            return
        try:
            fmt = buffer.format()
            if fmt.sampleFormat() not in (fmt.Int16, fmt.Int8, fmt.Int32, fmt.Float):
                return
            frames = buffer.frameCount()
            if frames <= 0:
                return
            data = buffer.data()
            import array, struct
            if fmt.sampleFormat() == fmt.Float:
                floats = [abs(struct.unpack_from('f', data, i)[0]) for i in range(0, len(data), 4)]
                if not floats: return
                rms = math.sqrt(sum(x*x for x in floats)/len(floats))
            elif fmt.sampleFormat() == fmt.Int16:
                arr = array.array('h')
                arr.frombytes(data[:len(data)//2*2])
                if not arr: return
                norm = [abs(x)/32768.0 for x in arr]
                rms = math.sqrt(sum(x*x for x in norm)/len(norm))
            elif fmt.sampleFormat() == fmt.Int8:
                arr = array.array('b')
                arr.frombytes(data)
                if not arr: return
                norm = [abs(x)/128.0 for x in arr]
                rms = math.sqrt(sum(x*x for x in norm)/len(norm))
            else:
                arr = array.array('i')
                arr.frombytes(data[:len(data)//4*4])
                if not arr: return
                norm = [abs(x)/2147483648.0 for x in arr]
                rms = math.sqrt(sum(x*x for x in norm)/len(norm))

            db = 20*math.log10(rms) if rms>0 else -120
            if db < self.silence_threshold_db:
                self._silence_ms += buffer.duration()/1000.0
                self.gap_status.setText(f"Silent {self._silence_ms:.1f}ms @ {db:.1f}dB")
                if self._silence_ms >= self.silence_min_duration*1000.0:
                    self._silence_ms = 0
                    p = self.current_player()
                    if p.duration() - p.position() < 12_000:
                        self.next_track()
                    else:
                        p.setPosition(min(p.position()+10_000, p.duration()-1000))
                        self.gap_status.setText("Skipped silence")
            else:
                self._silence_ms = 0
                self.gap_status.setText("Monitoring")
        except Exception:
            pass

class LibraryPanel(QWidget):
    request_scan = QtCore.Signal()
    request_search = QtCore.Signal(str, str)
    request_load_playlist = QtCore.Signal(str)
    request_save_playlist = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_scan = QPushButton("Scan Library")
        self.combo = QComboBox(); self.combo.addItems(["artist","title","album"])
        self.search = QLineEdit(); self.search.setPlaceholderText("Search…")
        self.btn_go = QPushButton("Search")
        top.addWidget(self.btn_scan); top.addWidget(self.combo); top.addWidget(self.search,1); top.addWidget(self.btn_go)
        lay.addLayout(top)

        self.status = QLabel("")
        lay.addWidget(self.status)

        self.playlists = QListWidget(); self.playlists.setSelectionMode(QListWidget.SingleSelection)
        self.btn_save_pl = QPushButton("Save Current Queue")
        lay.addWidget(QLabel("Playlists"))
        lay.addWidget(self.playlists,1)
        lay.addWidget(self.btn_save_pl)

        self.btn_scan.clicked.connect(self.request_scan.emit)
        self.btn_go.clicked.connect(self.on_go)
        self.btn_save_pl.clicked.connect(self.on_save)

        self.playlists.itemActivated.connect(self.on_pl_activated)

    def on_go(self):
        col = self.combo.currentText()
        q = self.search.text().strip()
        if q:
            self.request_search.emit(col, q)

    def on_save(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Playlist Name", "Name:")
        if ok and name.strip():
            self.request_save_playlist.emit(name.strip())

    def on_pl_activated(self, item: QListWidgetItem):
        pid = item.data(Qt.UserRole)
        self.request_load_playlist.emit(pid)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EcoG Qt Media Player")
        self.resize(1100, 720)

        # widgets
        self.library = LibraryPanel()
        self.player = PlayerWidget()
        self.tracks_list = QListWidget()

        # layout
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.addWidget(self.tracks_list, 1)
        rlay.addWidget(self.player, 0)

        splitter = QSplitter()
        splitter.addWidget(self.library); splitter.addWidget(right)
        splitter.setSizes([280, 820])
        self.setCentralWidget(splitter)

        # data
        self.library_index: list[Track] = []
        self.playlists = self.load_json(PLAYLISTS_FILE, default={"playlists":[]})
        self.settings  = self.load_json(SETTINGS_FILE, default={"crossfade":6})

        # connect signals
        self.library.request_scan.connect(self.scan_library)
        self.library.request_search.connect(self.search_tracks)
        self.library.request_load_playlist.connect(self.load_playlist_by_id)
        self.library.request_save_playlist.connect(self.save_current_playlist)
        self.player.request_reveal.connect(self.reveal_path)
        self.tracks_list.itemActivated.connect(self.play_from_selection)

        self.refresh_playlists()
        self.player.crossfade_seconds = int(self.settings.get("crossfade", 6))
        self.player.fade_slider.setValue(self.player.crossfade_seconds)

        self._current_search_results: list[Track] = []

    def load_json(self, path: Path, default):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return default

    def save_json(self, path: Path, obj):
        try:
            path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        except Exception:
            pass

    def scan_library(self):
        dirs = QFileDialog.getExistingDirectoryUrl(self, "Select Music Folder", QUrl.fromLocalFile(str(Path.home())))
        if not dirs.isLocalFile():
            return
        base = Path(dirs.toLocalFile())
        count = 0
        idx: list[Track] = []
        for root, _, files in os.walk(base):
            for f in files:
                p = Path(root)/f
                if p.suffix.lower() in AUDIO_EXTS:
                    track = read_metadata(p)
                    track.silences = analyze_silences(
                        str(p),
                        threshold_db = self.settings.get("silence_db", -46),
                        min_silence_ms = int(self.settings.get("silence_ms", 100))
                    )
                    idx.append(track)
                    count += 1
        self.library_index = idx
        self.library.status.setText(f"Scanned {count} files in {base}")
        self.show_tracks(idx)

    def show_tracks(self, tracks: list[Track]):
        self.tracks_list.clear()
        for t in tracks:
            item = QListWidgetItem(f"{t.artist or 'Unknown Artist'} — {t.title} ({t.album})")
            item.setData(Qt.UserRole, t)
            self.tracks_list.addItem(item)

    def search_tracks(self, column: str, query: str):
        q = query.lower()
        results = []
        for t in self.library_index:
            value = getattr(t, column, "") or ""
            if q in str(value).lower():
                results.append(t)
        self._current_search_results = results
        self.show_tracks(results)

        if results:
            self.player.set_playlist(results, start_index=0)

    def play_from_selection(self, item: QListWidgetItem):
        t = item.data(Qt.UserRole)
        if t is None: return
        tracks = self._current_search_results or self.library_index
        idx = 0
        for i, tr in enumerate(tracks):
            if tr.path == t.path:
                idx = i; break
        self.player.set_playlist(tracks, start_index=idx)

    def refresh_playlists(self):
        self.library.playlists.clear()
        for pl in self.playlists.get("playlists", []):
            item = QListWidgetItem(f"{pl['name']} ({len(pl['tracks'])})")
            item.setData(Qt.UserRole, pl["id"])
            self.library.playlists.addItem(item)

    def save_current_playlist(self, name: str):
        if not self.player.playlist:
            QMessageBox.information(self, "Playlists", "No current queue to save.")
            return
        pid = str(int(QtCore.QDateTime.currentMSecsSinceEpoch()))
        rec = {"id": pid, "name": name, "tracks":[asdict(t) for t in self.player.playlist]}
        self.playlists.setdefault("playlists", []).append(rec)
        self.save_json(PLAYLISTS_FILE, self.playlists)
        self.refresh_playlists()

    def load_playlist_by_id(self, pid: str):
        for pl in self.playlists.get("playlists", []):
            if pl["id"] == pid:
                tracks = [Track(**t) for t in pl["tracks"]]
                self.player.set_playlist(tracks, start_index=0)
                return

    def reveal_path(self, path: str):
        if sys.platform.startswith("win"):
            os.startfile(os.path.dirname(path))
        elif sys.platform == "darwin":
            os.system(f'open -R "{path}"')
        else:
            os.system(f'xdg-open "{os.path.dirname(path)}"')

def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

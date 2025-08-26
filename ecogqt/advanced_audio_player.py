import sys
import os
from PySide6.QtCore import Qt, QDate, QEvent, QUrl, Slot, QTimer, QSize, QRect
from PySide6.QtGui import QPixmap, QTextCursor, QImage, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QSlider, QListWidget, QFileDialog, QTextEdit, QListWidgetItem, QMessageBox,
    QGridLayout, QComboBox, QSpinBox, QFormLayout, QGroupBox
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaMetaData
from mutagen import File
from mutagen.flac import FLAC
import re

class SynchronizedLyrics:
    def __init__(self, audio_path=None):
        self.times = []
        self.lines = []
        self.raw_lyrics = ""
        if audio_path:
            lrc_path = os.path.splitext(audio_path)[0] + ".lrc"
            if os.path.exists(lrc_path):
                with open(lrc_path, encoding='utf-8') as f:
                    self.raw_lyrics = f.read()
            else:
                self.raw_lyrics = self.get_embedded_lyrics(audio_path)
            self.parse_lyrics(self.raw_lyrics)

    def get_embedded_lyrics(self, audio_path):
        audio = File(audio_path)
        if audio is None:
            return ""

            # FLAC/Vorbis
        if audio.__class__.__name__ == 'FLAC':
            for key in audio:
                if key.lower() in ('lyrics', 'unsyncedlyrics', 'lyric'):
                    return audio[key][0]
            return ""

            # MP3 (ID3)
        if hasattr(audio, 'tags') and audio.tags:
            # USLT (unsynchronized lyrics) is the standard for ID3
            for k in audio.tags.keys():
                if k.startswith('USLT') or k.startswith('SYLT'):
                    return str(audio.tags[k])
                if k.lower() in ('lyrics', 'unsyncedlyrics', 'lyric'):
                    return str(audio.tags[k])
            # MP4/AAC
        if hasattr(audio, 'tags') and hasattr(audio.tags, 'get'):
            if audio.tags.get('\xa9lyr'):
                return audio.tags['\xa9lyr'][0]
        return ""

    def parse_lyrics(self, lyrics_text):
        time_tag = re.compile(r"\[(\d+):(\d+)(?:\.(\d+))?\]")
        self.times = []
        self.lines = []
        for line in lyrics_text.splitlines():
            matches = list(time_tag.finditer(line))
            if matches:
                lyric = time_tag.sub('', line).strip()
                for m in matches:
                    min, sec, ms = m.groups()
                    total_ms = int(min) * 60 * 1000 + int(sec) * 1000 + int(ms or 0)
                    self.times.append(total_ms)
                    self.lines.append(lyric)
            elif line.strip():
                self.times.append(0)
                self.lines.append(line.strip())

    def get_current_line(self, pos_ms):
        for i, t in enumerate(self.times):
            if pos_ms < t:
                return max(0, i-1)
        return len(self.lines)-1 if self.lines else -1

    def is_synchronized(self):
        """Return True if lyrics are synchronized (have time tags)."""
        # Synchronized if any time tag is nonzero
        return any(t > 0 for t in self.times)

class LyricsDisplay(QTextEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setReadOnly(True)
        self.setStyleSheet("font-size: 18px;")
        self.current_line_idx = -1
        self.lines = []
        self.is_synchronized = False

    def set_lyrics(self, lines, is_synchronized):
        self.lines = lines
        self.is_synchronized = is_synchronized
        self.current_line_idx = -1
        self.update_display(-1)

    def update_display(self, highlight_idx):
        html = ""
        for idx, line in enumerate(self.lines):
            if self.is_synchronized and idx == highlight_idx:
                html += f"<div style='color: #3A89FF; font-weight: bold; background: #E0F0FF'>{line}</div>"
            else:
                html += f"<div>{line}</div>"
        self.setHtml(html)
        if self.is_synchronized and 0 <= highlight_idx < len(self.lines):
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.Start)
            for _ in range(highlight_idx):
                cursor.movePosition(QTextCursor.Down)
            self.setTextCursor(cursor)
            self.ensureCursorVisible()

    def highlight_line(self, idx):
        # Only highlight if lyrics are synchronized
        if self.is_synchronized:
            if idx != self.current_line_idx:
                self.current_line_idx = idx
                self.update_display(idx)
        else:
            # For unsynchronized lyrics, never highlight any line
            if self.current_line_idx != -1:
                self.current_line_idx = -1
                self.update_display(-1)

def split_image(image_path, tile_width, tile_height):
    image = QImage(image_path)
    if image.isNull():
        print("Failed to load image:", image_path)
        return []

    img_width = image.width()
    img_height = image.height()
    sub_images = []

    for top in range(0, img_height, tile_height):
        for left in range(0, img_width, tile_width):
            rect = QRect(left, top, min(tile_width, img_width - left), min(tile_height, img_height - top))
            sub_img = image.copy(rect)
            sub_images.append(sub_img)
    return sub_images

class AudioPlayer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ultimate Media Player")
        self.resize(1200, 800)
        self.playlist = []
        self.current_index = -1
        self.show_remaining = False
        self.lyrics = None
        self.lyrics_timer = QTimer(self)
        self.lyrics_timer.setInterval(200)
        self.lyrics_timer.timeout.connect(self.update_lyrics_display)

        # Mixing/transition config
        self.mix_method = "Fade"  # "Fade" or "Cue"
        self.transition_duration = 4  # seconds

        # Audio/Player
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(1.0)  # or any value between 0.0 (mute) to 1.0 (full volume)
        self.player.setAudioOutput(self.audio_output)
        self.next_player = None  # For mixing with next track
        self.next_output = None

        # Silence elimination config
        self.skip_silence = True  # Optionally configurable

        # Playlist browser with context menu support
        self.playlist_widget = QListWidget()
        self.playlist_label = QLabel("Playlist")
        self.playlist_widget.setDragDropMode(QListWidget.InternalMove)
        self.playlist_widget.itemDoubleClicked.connect(
            self.play_selected_track)
        self.playlist_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.playlist_widget.customContextMenuRequested.connect(
            self.show_playlist_context_menu)

        # Drag-and-drop support
        self.setAcceptDrops(True)
        self.playlist_widget.viewport().setAcceptDrops(True)
        self.playlist_widget.viewport().installEventFilter(self)

        # Controls/UI
        self.album_art = QLabel(); self.album_art.setFixedSize(256, 256)
        self.album_art.setScaledContents(True)
        self.title_label = QLabel("-- Title --")
        self.artist_label = QLabel("-- Artist --")
        self.album_label = QLabel("-- Album --")
        self.year_label = QLabel("-- Year --")
        self.codec_label = QLabel("-- Codec --")

        self.time_label = QPushButton("--:--"); self.time_label.setFlat(True)
        self.time_label.setCursor(Qt.PointingHandCursor)
        self.time_label.clicked.connect(self.toggle_time_display)
        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(0, 100)
      #  self.slider.sliderMoved.connect(self.seek_position) replaced by slider released
        self.slider.sliderMoved.connect(self.on_slider_moved)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self._slider_moving = False

        image_path = 'static/images/buttons.jpg'
        tile_width = 1650
        tile_height = 1650
        self.sub_images = split_image(image_path, tile_width, tile_height)

        self.prev_button = QPushButton()
        pixmap = QPixmap.fromImage(self.sub_images[2])
        self.prev_button.setIcon(QIcon(pixmap))
        self.prev_button.setIconSize(QSize(50, 50))
        self.prev_button.setFixedSize(QSize(50, 50))
        self.play_button = QPushButton()
        pixmap = QPixmap.fromImage(self.sub_images[0])
        self.play_button.setIcon(QIcon(pixmap))
        self.play_button.setIconSize(QSize(50, 50))
        self.play_button.setFixedSize(QSize(50, 50))
        self.next_button = QPushButton()
        pixmap = QPixmap.fromImage(self.sub_images[3])
        self.next_button.setIcon(QIcon(pixmap))
        self.next_button.setIconSize(QSize(50, 50))
        self.next_button.setFixedSize(QSize(50, 50))
        self.prev_button.clicked.connect(self.prev_track)
        self.play_button.clicked.connect(self.toggle_play_pause)
        self.next_button.clicked.connect(self.next_track)

        # Lyrics
        self.lyrics_display = LyricsDisplay(); self.lyrics_display.setReadOnly(True)

        # Mixing Controls UI
        self.mix_method_combo = QComboBox()
        self.mix_method_combo.addItems(["Fade", "Cue"])
        self.mix_method_combo.setCurrentText(self.mix_method)
        self.mix_method_combo.currentTextChanged.connect(self.set_mix_method)

        self.transition_spin = QSpinBox()
        self.transition_spin.setRange(1, 10)
        self.transition_spin.setValue(self.transition_duration)
        self.transition_spin.setSuffix(" s")
        self.transition_spin.valueChanged.connect(self.set_transition_duration)

        self.silence_check = QPushButton("Skip Silence")
        self.silence_check.setCheckable(True)
        self.silence_check.setChecked(self.skip_silence)
        self.silence_check.toggled.connect(self.set_skip_silence)

        mix_form = QFormLayout()
        mix_form.addRow("Mix Method:", self.mix_method_combo)
        mix_form.addRow("Transition:", self.transition_spin)
        mix_form.addRow(self.silence_check)
        mix_group = QGroupBox("Mixing Options")
        mix_group.setLayout(mix_form)

        # Layout
        info_layout = QHBoxLayout()
        info_layout.addWidget(self.album_art)
        meta_layout = QVBoxLayout()
        meta_layout.addWidget(self.title_label)
        meta_layout.addWidget(self.artist_label)
        meta_layout.addWidget(self.album_label)
        meta_layout.addWidget(self.year_label)
        meta_layout.addWidget(self.codec_label)
        info_layout.addLayout(meta_layout)

        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.time_label)
        progress_layout.addWidget(self.slider)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.prev_button)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.next_button)

        left_layout = QVBoxLayout()
        left_layout.addLayout(info_layout)
        left_layout.addLayout(progress_layout)
        left_layout.addLayout(controls_layout)
        left_layout.addWidget(self.lyrics_display)
        left_layout.addWidget(mix_group)

        playlist_layout = QVBoxLayout()
        playlist_layout.addWidget(self.playlist_label)
        playlist_layout.addWidget(self.playlist_widget)
        main_layout = QHBoxLayout(self)
        main_layout.addLayout(playlist_layout, 1)
        main_layout.addLayout(left_layout, 2)
        self.setLayout(main_layout)

        # Connections
        self.player.positionChanged.connect(self.update_slider)
        self.player.durationChanged.connect(self.update_duration)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.player.playbackStateChanged.connect(self.update_play_button)
        self.slider.sliderPressed.connect(lambda: self.player.pause())
        self.slider.sliderReleased.connect(lambda: self.player.play())
        self.player.metaDataChanged.connect(self.update_metadata)
        self.player.errorOccurred.connect(self.handle_error)

        # For mixing (transition to next track)
        self.player.positionChanged.connect(self.check_for_mix_transition)

        # Init
        self.update_play_button()
        self.show()

    # --- Mixing/transition config slots ---
    def set_mix_method(self, method):
        self.mix_method = method

    def set_transition_duration(self, val):
        self.transition_duration = val

    def set_skip_silence(self, val):
        self.skip_silence = val

    # --- Mixing logic ---
    def check_for_mix_transition(self, position):
        """Trigger mix/cue if current track is close to ending."""
        duration = self.player.duration()
        if duration <= 0 or self.current_index >= len(self.playlist) - 1:
            return

        # If fade/cue not yet started, and we're within transition_duration
        if duration - position <= self.transition_duration * 1000:
            if not hasattr(self, "_mixing_next") or not self._mixing_next:
                self._mixing_next = True
                if self.mix_method == "Fade":
                    self.start_fade_to_next()
                else:
                    self.cue_next_track()
        else:
            self._mixing_next = False

    def start_fade_to_next(self):
        """Fade out current track and fade in next track."""
        next_idx = self.current_index + 1
        if not (0 <= next_idx < len(self.playlist)):
            return
        next_path = self.playlist[next_idx]
        # Prepare second player
        self.next_player = QMediaPlayer(self)
        self.next_output = QAudioOutput(self)
        self.next_player.setAudioOutput(self.next_output)
        self.next_player.setSource(QUrl.fromLocalFile(next_path))
        self.next_output.setVolume(0)
        self.next_player.play()

        self.fade_timer = QTimer(self)
        self.fade_timer.setInterval(100)
        fade_steps = int(self.transition_duration * 1000 / 100)
        self._fade_step = 0

        def fade():
            self._fade_step += 1
            frac = self._fade_step / fade_steps
            self.audio_output.setVolume(max(0, 1.0 - frac))
            self.next_output.setVolume(min(1.0, frac))
            if self._fade_step >= fade_steps:
                self.fade_timer.stop()
                self.audio_output.setVolume(1.0)
                self.player.stop()
                # Switch to next player
                self.player = self.next_player
                self.audio_output = self.next_output
                self.current_index = next_idx
                self.load_track(self.current_index, auto_play=False, skip_mix_check=True)
                self.player.play()
                self.player.positionChanged.connect(self.update_slider)
                self.player.durationChanged.connect(self.update_duration)
                self.player.mediaStatusChanged.connect(self.media_status_changed)
                self.player.playbackStateChanged.connect(self.update_play_button)
                self.player.metaDataChanged.connect(self.update_metadata)
                self.player.errorOccurred.connect(self.handle_error)
                self.player.positionChanged.connect(self.check_for_mix_transition)
                self.next_player = None
                self.next_output = None
                self._mixing_next = False

        self.fade_timer.timeout.connect(fade)
        self.fade_timer.start()

    def cue_next_track(self):
        """Cue next track directly at transition point, skipping silence if enabled."""
        next_idx = self.current_index + 1
        if not (0 <= next_idx < len(self.playlist)):
            return
        # Jump to next track, optionally skip silence at start
        self.load_track(next_idx, skip_silence=self.skip_silence)

    # Slightly changed signature to support mixing
    def load_track(self, idx, auto_play=True, skip_mix_check=False,
                   skip_silence=False):
        if 0 <= idx < len(self.playlist):
            path = self.playlist[idx]
            self.current_index = idx
            self.player.setSource(QUrl.fromLocalFile(path))
            self.slider.setValue(0)
            self.title_label.setText(os.path.basename(path))
            self.artist_label.setText("-- Artist --")
            self.album_label.setText("-- Album --")
            self.year_label.setText("-- Date --")
            self.codec_label.setText("-- Date --")
            self.set_album_art(path)
            self.load_lyrics(path)
            if auto_play:
                self.player.play()
            self.lyrics_timer.start()
            self.playlist_widget.setCurrentRow(idx)
            # Optionally skip silence at start (very basic, see note below)
            if skip_silence:
                QTimer.singleShot(500, self.skip_leading_silence)
            # Reset mix triggers unless skip_mix_check (for fade handover)
            if not skip_mix_check:
                self._mixing_next = False
        else:
            self.title_label.setText("No Track Loaded")
            self.artist_label.setText("--")
            self.album_label.setText("--")
            self.year_label.setText("--")
            self.codec_label.setText("--")
            self.album_art.setPixmap(QPixmap())
            self.lyrics_display.clear()
            self.lyrics_timer.stop()

    def skip_leading_silence(self):
        """A very basic silence-skip: jump forward if amplitude is zero (needs real audio analysis for best results)."""
        # QMediaPlayer does NOT support sample-level analysis.
        # A real solution would use something like pydub or ffmpeg to find silence start/end.
        # Here, we just jump ahead 1s, up to 4 times, if still at start.
        max_tries = 4
        for i in range(max_tries):
            if self.player.position() < 1500:
                self.player.setPosition(self.player.position() + 1000)
            else:
                break

    # Drag-and-drop support
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        self.add_files(files)

    def eventFilter(self, source, event):
        if source == self.playlist_widget.viewport() and event.type() == QEvent.Drop:
            files = [u.toLocalFile() for u in event.mimeData().urls()]
            self.add_files(files)
            return True
        return super().eventFilter(source, event)

    def show_playlist_menu(self, pos=None):
        menu = QFileDialog(self)
        menu.setFileMode(QFileDialog.ExistingFiles)
        menu.setNameFilters(["Playlists (*.m3u *.m3u8 *.cue)", "Audio files (*.mp3 *.flac *.ogg *.wav *.m4a)", "All files (*)"])
        if menu.exec():
            self.add_files(menu.selectedFiles())

    def add_files(self, files):
        for f in files:
            if os.path.isfile(f):
                ext = os.path.splitext(f)[1].lower()
                if ext in ['.m3u', '.m3u8']:
                    pl = self.load_m3u_playlist(f)
                    self.playlist += pl
                    for i in pl:
                        item = QListWidgetItem(os.path.basename(i))
                        self.playlist_widget.addItem(item)
                elif ext == '.cue':
                    pl = self.load_cue_playlist(f)
                    self.playlist += pl
                    for i in pl:
                        item = QListWidgetItem(os.path.basename(i))
                        self.playlist_widget.addItem(item)
                else:
                    self.playlist.append(f)
                    item = QListWidgetItem(os.path.basename(f))
                    self.playlist_widget.addItem(item)
        if self.current_index == -1 and self.playlist:
            self.load_track(0)

    def load_m3u_playlist(self, path):
        tracks = []
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                tracks.append(
                    os.path.abspath(os.path.join(os.path.dirname(path), line)))
        return tracks

    def load_cue_playlist(self, path):
        tracks = []
        with open(path, encoding='utf-8') as f:
            for line in f:
                if re.match('^FILE .(.*). (.*)$', line):
                    file_path = line[6:-7]
                    if not os.path.isabs(file_path):
                        file_path = os.path.abspath(
                            os.path.join(os.path.dirname(path), file_path))
                    tracks.append(file_path)
        return tracks

    def play_selected_track(self, item):
        idx = self.playlist_widget.row(item)
        self.load_track(idx)

    def show_playlist_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self.playlist_widget)
        add_action = menu.addAction("Add Files")
        remove_action = menu.addAction("Remove Selected")
        clear_action = menu.addAction("Clear Playlist")
        action = menu.exec(self.playlist_widget.mapToGlobal(pos))
        if action == add_action:
            self.show_playlist_menu(pos)
        elif action == remove_action:
            self.remove_selected_item()
        elif action == clear_action:
            self.clear_playlist()

    def remove_selected_item(self):
        row = self.playlist_widget.currentRow()
        if row >= 0:
            self.playlist_widget.takeItem(row)
            del self.playlist[row]
            # Adjust current_index if necessary
            if row == self.current_index:
                self.player.stop()
                self.current_index = -1
                self.lyrics_display.clear()
            elif row < self.current_index:
                self.current_index -= 1

    def clear_playlist(self):
        self.playlist_widget.clear()
        self.playlist.clear()
        self.player.stop()
        self.current_index = -1
        self.lyrics_display.clear()

    def set_album_art(self, path):
        # Use mutagen to extract artwork robustly
        img_data = None
        try:
            audio = File(path)
            if audio is not None:
                if hasattr(audio, 'tags'):
                    tags = audio.tags
                    # MP3: APIC
                    if 'APIC:' in tags:
                        img_data = tags['APIC:'].data
                    # MP4/M4A: covr
                    elif hasattr(tags, 'get') and tags.get('covr'):
                        img_data = tags['covr'][0]
                    # FLAC: pictures
                    elif hasattr(audio, 'pictures') and audio.pictures:
                        img_data = audio.pictures[0].data
            if img_data:
                img = QImage.fromData(img_data)
                pix = QPixmap.fromImage(img)
                self.album_art.setPixmap(pix.scaled(self.album_art.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        except Exception as e:
            print("Artwork extraction error:", e)
        # Fallback
        self.album_art.setPixmap(QPixmap("static/images/default_album_art.png") if os.path.exists("static/images/default_album_art.png") else QPixmap())

    def load_lyrics(self, audio_path):
        self.lyrics = SynchronizedLyrics(audio_path)
        self.lyrics_display.set_lyrics(self.lyrics.lines, self.lyrics.is_synchronized())
        self.update_lyrics_display()

    def update_lyrics_display(self):
        if self.lyrics and self.lyrics.lines:
            idx = self.lyrics.get_current_line(
                self.player.position()) if self.lyrics.is_synchronized() else -1
            self.lyrics_display.highlight_line(idx)
        else:
            self.lyrics_display.setText("No lyrics found.")


    def prev_track(self):
        if self.current_index > 0:
            self.load_track(self.current_index - 1)

    def next_track(self):
        if self.current_index < len(self.playlist) - 1:
            self.load_track(self.current_index + 1)

    def toggle_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def update_play_button(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            pixmap = QPixmap.fromImage(self.sub_images[1])
            self.play_button.setIcon(QIcon(pixmap))
        else:
            pixmap = QPixmap.fromImage(self.sub_images[0])
            self.play_button.setIcon(QIcon(pixmap))

    def update_slider(self, position):
        duration = self.player.duration()
        if duration > 0:
            value = int((position / duration) * 100)
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)
        self.update_time_label(position, duration)

    def on_slider_moved(self, value):
        self._slider_moving = True  # Mark that user is dragging

    def on_slider_released(self):
        if self._slider_moving:
            duration = self.player.duration()
            value = self.slider.value()
            if duration > 0:
                new_position = int((value / 100) * duration)
                self.player.setPosition(new_position)
        self._slider_moving = False

    def update_duration(self, duration):
        self.slider.setEnabled(duration > 0)
        self.update_time_label(self.player.position(), duration)

    def seek_position(self, value):
        duration = self.player.duration()
        if duration > 0:
            new_position = int((value / 100) * duration)
            self.player.setPosition(new_position)

    def update_time_label(self, position, duration):
        if self.show_remaining:
            remaining = max(0, duration - position)
            self.time_label.setText("-" + self.format_time(remaining))
        else:
            self.time_label.setText(self.format_time(position))

    def toggle_time_display(self):
        self.show_remaining = not self.show_remaining
        self.update_time_label(self.player.position(), self.player.duration())

    def media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            # If mixing/cue-in is active, do not trigger next_track (already handled)
            if hasattr(self, "_mixing_next") and self._mixing_next:
                return
            self.next_track()

    def handle_error(self, error, error_string):
        if error != QMediaPlayer.NoError:
            self.next_track()
            print("Playback Error:", error_string)

    def update_metadata(self):
        meta = self.player.metaData()
        title = meta.stringValue(QMediaMetaData.Title) or os.path.basename(self.playlist[self.current_index])
        artist = meta.stringValue(QMediaMetaData.AlbumArtist) or meta.stringValue(QMediaMetaData.Author) or "--"
        album = meta.stringValue(QMediaMetaData.AlbumTitle) or "--"
        year = self.extract_year(meta) or "--"
        self.title_label.setText('Title: ' + title)
        self.artist_label.setText('Artist: ' + artist)
        self.album_label.setText('Album: '+ album)
        self.year_label.setText('Year: ' + year)
        self.codec_label.setText(self.extract_audio_info())

    @staticmethod
    def format_time(ms):
        s = ms // 1000
        m, s = divmod(s, 60)
        return f"{m:02}:{s:02}"

    def extract_year(self, meta):
        # Try from Qt meta first (works for MP3/MP4, rarely for FLAC)
        date_val = meta.value(QMediaMetaData.Date) if hasattr(meta,
                                                              "value") else None
        if date_val:
            if isinstance(date_val, QDate):
                return str(date_val.year())
            if isinstance(date_val, str):
                match = re.search(r'\b(\d{4})\b', date_val)
                if match:
                    return match.group(1)
            date_str = str(date_val)
            match = re.search(r'\b(\d{4})\b', date_str)
            if match:
                return match.group(1)
        # If FLAC, try mutagen
        audio_path = self.playlist[self.current_index]
        if audio_path.lower().endswith(".flac"):
            try:
                audio = FLAC(audio_path)
                for tag in ("date", "year", "year_released"):
                    if tag in audio:
                        return audio[tag][0][:4]
            except Exception:
                pass
        return "--"

    def extract_audio_info(self):
        audio = File(self.playlist[self.current_index])
        if not audio:
            print("Unsupported or corrupted file.")
            return

        # Codec
        codec = audio.mime[0] if hasattr(audio,
                                         'mime') and audio.mime else audio.__class__.__name__

        # Sample rate and bitrate
        sample_rate = getattr(audio.info, 'sample_rate', None)
        bits = getattr(audio.info, 'bits_per_sample', None)
        bitrate = getattr(audio.info, 'bitrate', None)
        if codec == 'audio/mp3':
            return codec + ' ' + str(sample_rate/1000) + 'kHz ' + str(round(bitrate/1000)) + 'kbps'
        else:
            return codec + ' ' + str(sample_rate/1000) + 'kHz/' + str(round(bits)) + 'bits  ' + str(round(bitrate/1000)) + 'kbps'


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = AudioPlayer()
    sys.exit(app.exec())
import sys
import os
from PySide6.QtCore import Qt, QUrl, Slot, QTimer, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider, QFileDialog, QTextEdit
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaMetaData

class AdvancedAudioPlayer(QWidget):
    def __init__(self, playlist):
        super().__init__()
        self.setWindowTitle("Advanced Media Player")
        self.playlist = playlist
        self.current_index = 0
        self.show_remaining = False
        self.lyrics = {}

        # Player and audio
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        # Main UI
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Track info and image
        info_layout = QHBoxLayout()
        self.album_art = QLabel()
        self.album_art.setFixedSize(128, 128)
        self.album_art.setScaledContents(True)
        info_layout.addWidget(self.album_art)

        meta_layout = QVBoxLayout()
        self.title_label = QLabel("-- Title --")
        self.artist_label = QLabel("-- Artist --")
        self.album_label = QLabel("-- Album --")
        meta_layout.addWidget(self.title_label)
        meta_layout.addWidget(self.artist_label)
        meta_layout.addWidget(self.album_label)
        info_layout.addLayout(meta_layout)
        main_layout.addLayout(info_layout)

        # Progress and time counter
        progress_layout = QHBoxLayout()
        self.time_label = QPushButton("--:--")
        self.time_label.setFlat(True)
        self.time_label.setCursor(Qt.PointingHandCursor)
        self.time_label.clicked.connect(self.toggle_time_display)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.sliderMoved.connect(self.seek_position)
        progress_layout.addWidget(self.time_label)
        progress_layout.addWidget(self.slider)
        main_layout.addLayout(progress_layout)

        # Control buttons
        controls_layout = QHBoxLayout()
        self.prev_button = QPushButton("⏮")
        self.play_button = QPushButton("⏯")
        self.next_button = QPushButton("⏭")
        controls_layout.addWidget(self.prev_button)
        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.next_button)
        main_layout.addLayout(controls_layout)

        # Lyrics display
        self.lyrics_display = QTextEdit()
        self.lyrics_display.setReadOnly(True)
        self.lyrics_display.setFixedHeight(100)
        main_layout.addWidget(self.lyrics_display)

        # Signals
        self.prev_button.clicked.connect(self.prev_track)
        self.play_button.clicked.connect(self.toggle_play_pause)
        self.next_button.clicked.connect(self.next_track)
        self.player.positionChanged.connect(self.update_slider)
        self.player.durationChanged.connect(self.update_duration)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.player.playbackStateChanged.connect(self.update_play_button)
        self.slider.sliderPressed.connect(lambda: self.player.pause())
        self.slider.sliderReleased.connect(lambda: self.player.play())
        self.player.metaDataChanged.connect(self.update_metadata)
        self.player.errorOccurred.connect(self.handle_error)

        # Load initial track
        self.load_track(self.current_index)

    def load_track(self, index):
        if 0 <= index < len(self.playlist):
            filepath = self.playlist[index]
            url = QUrl.fromLocalFile(filepath)
            self.player.setSource(url)
            self.slider.setValue(0)
            self.title_label.setText(os.path.basename(filepath))
            self.artist_label.setText("-- Artist --")
            self.album_label.setText("-- Album --")
            self.album_art.setPixmap(QPixmap())
            self.load_lyrics(filepath)
            self.lyrics_display.setText(self.lyrics.get(filepath, ""))
            self.player.play()
        else:
            self.title_label.setText("No Track Loaded")
            self.artist_label.setText("--")
            self.album_label.setText("--")
            self.album_art.setPixmap(QPixmap())
            self.lyrics_display.clear()

    def update_metadata(self):
        meta = self.player.metaData()
        title = meta.stringValue(QMediaMetaData.Title) or os.path.basename(self.playlist[self.current_index])
        artist = meta.stringValue(QMediaMetaData.AlbumArtist) or meta.stringValue(QMediaMetaData.Author) or "--"
        album = meta.stringValue(QMediaMetaData.AlbumTitle) or "--"
        self.title_label.setText(title)
        self.artist_label.setText(artist)
        self.album_label.setText(album)
        img = meta.value(QMediaMetaData.CoverArtImage)
        if img:
            pix = QPixmap.fromImage(img)
            self.album_art.setPixmap(pix.scaled(self.album_art.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.album_art.setPixmap(QPixmap("default_album_art.png") if os.path.exists("default_album_art.png") else QPixmap())

    def prev_track(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.load_track(self.current_index)

    def next_track(self):
        if self.current_index < len(self.playlist) - 1:
            self.current_index += 1
            self.load_track(self.current_index)

    def toggle_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def update_play_button(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.play_button.setText("⏸")
        else:
            self.play_button.setText("▶")

    def update_slider(self, position):
        duration = self.player.duration()
        if duration > 0:
            value = int((position / duration) * 100)
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)
        self.update_time_label(position, duration)

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
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.EndOfMedia:
            self.next_track()

    def handle_error(self, error, error_string):
        if error != QMediaPlayer.NoError:
            self.title_label.setText("Error: " + error_string)
            self.player.stop()

    def load_lyrics(self, audio_path):
        # Try to load .lrc with same basename
        lrc_path = os.path.splitext(audio_path)[0] + ".lrc"
        if os.path.exists(lrc_path):
            with open(lrc_path, "r", encoding="utf-8") as f:
                self.lyrics[audio_path] = f.read()
        else:
            self.lyrics[audio_path] = "No lyrics found for this track."

    @staticmethod
    def format_time(ms):
        s = ms // 1000
        m, s = divmod(s, 60)
        return f"{m:02}:{s:02}"

if __name__ == "__main__":
    # Replace with your actual files (optionally with .lrc for lyrics and album art in metadata)
    playlist = [
        "D:\EcoG\Music//2. Playlist//4AD_ The First Five Years//FLAC (16bit-44.1kHz)//Bauhaus - Dark Entries.flac",
        "D:\EcoG\Music//2. Playlist//4AD_ The First Five Years//FLAC (16bit-44.1kHz)//This Mortal Coil - Another Day (Remastered).flac",
        "D:\EcoG\Music//2. Playlist//00er Pop Italiano Essentials//01. Jovanotti - Mi Fido Di Te.m4a",
        "D:\EcoG\Music//2. Playlist//00er Pop Italiano Essentials//05. Giusy Ferreri - Non Ti Scordar Mai Di Me.m4a",
        "D:\EcoG\Music//2. Playlist//00er Pop Italiano Essentials//17. Laura Pausini - E Ritorno Da Te.m4a"
    ]
    app = QApplication(sys.argv)
    player = AdvancedAudioPlayer(playlist)
    player.resize(500, 350)
    player.show()
    sys.exit(app.exec())
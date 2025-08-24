from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel
)
from PySide6.QtCore import Qt, QUrl, Slot, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

class SimpleAudioPlayer(QWidget):
    def __init__(self, playlist):
        super().__init__()
        self.setWindowTitle("Media Player Example")
        self.playlist = playlist
        self.current_index = 0

        # QMediaPlayer and Audio Output
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)

        # UI Widgets
        self.label = QLabel("No Track Loaded")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.sliderMoved.connect(self.seek_position)

        self.prev_button = QPushButton("Previous")
        self.play_button = QPushButton("Pause")
        self.next_button = QPushButton("Next")

        layout = QVBoxLayout()
        layout.addWidget(self.label)
        layout.addWidget(self.slider)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.prev_button)
        button_layout.addWidget(self.play_button)
        button_layout.addWidget(self.next_button)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        # Signals
        self.prev_button.clicked.connect(self.prev_track)
        self.play_button.clicked.connect(self.toggle_play_pause)
        self.next_button.clicked.connect(self.next_track)
        self.player.positionChanged.connect(self.update_slider)
        self.player.durationChanged.connect(self.update_duration)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.slider.sliderPressed.connect(lambda: self.player.pause())
        self.slider.sliderReleased.connect(lambda: self.player.play())

        self.load_track(self.current_index)

    def load_track(self, index):
        if 0 <= index < len(self.playlist):
            self.player.setSource(QUrl.fromLocalFile(self.playlist[index]))
            self.label.setText(f"Playing: {self.playlist[index]}")
            self.player.play()
            self.play_button.setText("Pause")
        else:
            self.label.setText("No Track Loaded")

    @Slot()
    def prev_track(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.load_track(self.current_index)

    @Slot()
    def next_track(self):
        if self.current_index < len(self.playlist) - 1:
            self.current_index += 1
            self.load_track(self.current_index)

    @Slot()
    def toggle_play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_button.setText("Play")
        else:
            self.player.play()
            self.play_button.setText("Pause")

    @Slot()
    def update_slider(self, position):
        duration = self.player.duration()
        if duration > 0:
            value = int((position / duration) * 100)
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)

    @Slot()
    def update_duration(self, duration):
        self.slider.setEnabled(duration > 0)

    @Slot()
    def seek_position(self, value):
        duration = self.player.duration()
        if duration > 0:
            new_position = int((value / 100) * duration)
            self.player.setPosition(new_position)

    @Slot()
    def media_status_changed(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.next_track()

if __name__ == "__main__":
    import sys
    # Replace these paths with actual local audio file paths
    playlist = [
        "D:\EcoG\Music//2. Playlist//4AD_ The First Five Years//FLAC (16bit-44.1kHz)//Bauhaus - Dark Entries.flac",
        "D:\EcoG\Music//2. Playlist//4AD_ The First Five Years//FLAC (16bit-44.1kHz)//This Mortal Coil - Another Day (Remastered).flac",
        "D:\EcoG\Music//2. Playlist//00er Pop Italiano Essentials//01. Jovanotti - Mi Fido Di Te.m4a",
        "D:\EcoG\Music//2. Playlist//00er Pop Italiano Essentials//05. Giusy Ferreri - Non Ti Scordar Mai Di Me.m4a",
        "D:\EcoG\Music//2. Playlist//00er Pop Italiano Essentials//17. Laura Pausini - E Ritorno Da Te.m4a"
    ]
    app = QApplication(sys.argv)
    player = SimpleAudioPlayer(playlist)
    player.resize(400, 120)
    player.show()
    sys.exit(app.exec())
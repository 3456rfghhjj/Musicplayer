# Music Player

A lightweight, dark-green themed desktop audio and video player built with Python, CustomTkinter, PyAV, and SoundDevice.

## Features
- **Broad Format Support:** Plays `.mp3`, `.flac`, `.m4a`, `.wav`, `.alac`, `.ogg`, `.aac`, `.opus`, `.aiff`, `.dsf`, `.mp4`, `.mkv`, `.mov`, and more via FFmpeg (`av`).
- **Real-Time Spectrum Analyzer:** 12-band logarithmic FFT visualizer with dynamic color-coded level bars.
- **Dual Mono Detection:** Automatically distinguishes true Stereo from 2-channel Dual Mono tracks.
- **Playlist Management:** Load individual files, recursively scan folders, or pass file paths via command line / macOS Finder.
- **Keyboard Shortcuts:** Control playback quickly using `Space` (Play/Pause), `Right` (Next track), and `Left` (Previous track / Seek to start).

## Installation
```bash
pip install -r requirements.txt
```

## Usage
```bash
python Music_player.py
```

import os
import sys
import threading
import time
from tkinter import filedialog
import av
import customtkinter as ctk
import numpy as np
import sounddevice as sd

# Color palette (dark green theme)
BG_MAIN = "#1E2A22"
BG_PANEL = "#25352B"
BG_ITEM = "#2E4A34"
BG_ITEM_ACTIVE = "#3E6B48"
ACCENT = "#4CAF50"
ACCENT_HOVER = "#45A049"
TEXT_MAIN = "#E8F5E9"
TEXT_DIM = "#A5D6A7"

# Colors for the separated bar analyzer on the right
SPEC_BG = "#141D17"
SPEC_COLOR_LOW = "#4CAF50"     # Green
SPEC_COLOR_MID = "#CDDC39"     # Yellow-green
SPEC_COLOR_HIGH = "#FF7043"    # Orange-red

SUPPORTED_EXTENSIONS = (
    ".mp3", ".flac", ".m4a", ".wav", ".alac", ".ogg", ".aac", ".wma",
    ".aiff", ".aif", ".opus", ".wv", ".ape", ".mka", ".ac3", ".dts",
    ".dsf", ".dff", ".mp4", ".mkv", ".mov", ".webm"
)


class AudioPlayer(ctk.CTk):
    def __init__(self, initial_files=None):
        super().__init__()

        self.title("Audio Player")
        self.geometry("780x500")
        self.minsize(560, 380)
        self.resizable(True, True)
        self.configure(fg_color=BG_MAIN)

        # Playback state
        self.playlist = []
        self.current_index = -1
        self.is_playing = False
        self.is_paused = False
        self.stop_event = threading.Event()
        self.seek_requested_time = None
        self.volume = 0.8
        self.current_time = 0.0
        self.total_duration = 0.0
        self.slider_dragging = False
        self.audio_thread = None
        self._track_finished = False

        # Data for the 12-band separated bar analyzer on the right
        self.num_bars = 12
        self.fft_size = 4096
        self.audio_ring = np.zeros(self.fft_size, dtype=np.float32)
        self.spec_target = np.zeros(self.num_bars, dtype=np.float32)
        self.spec_current = np.zeros(self.num_bars, dtype=np.float32)
        self.spec_lock = threading.Lock()
        self._fft_window = np.hanning(self.fft_size).astype(np.float32)
        self.spec_bars = []

        self._create_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_resize)
        self.bind("<space>", lambda _e: self.toggle_play_pause())
        self.bind("<Right>", lambda _e: self.next_track())
        self.bind("<Left>", lambda _e: self.previous_track())

        if initial_files:
            self.add_to_playlist(initial_files, play_first=True)

        self.after(150, self._update_ui_loop)
        self.after(33, self._draw_spectrum)

    def _create_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # 1. Top panel with file loading buttons
        top_panel = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=8)
        top_panel.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        top_panel.grid_columnconfigure(2, weight=1)

        btn_files = ctk.CTkButton(
            top_panel, text="Open Files", fg_color=BG_ITEM,
            hover_color=BG_ITEM_ACTIVE, text_color=TEXT_MAIN,
            width=130, command=self.select_files
        )
        btn_files.grid(row=0, column=0, padx=(10, 6), pady=10, sticky="w")

        btn_folder = ctk.CTkButton(
            top_panel, text="Open Folder", fg_color=BG_ITEM,
            hover_color=BG_ITEM_ACTIVE, text_color=TEXT_MAIN,
            width=140, command=self.select_folder
        )
        btn_folder.grid(row=0, column=1, padx=6, pady=10, sticky="w")

        btn_clear = ctk.CTkButton(
            top_panel, text="Clear", fg_color="transparent",
            border_width=1, border_color=BG_ITEM_ACTIVE,
            hover_color=BG_ITEM, text_color=TEXT_DIM,
            width=85, command=self.clear_playlist
        )
        btn_clear.grid(row=0, column=3, padx=10, pady=10, sticky="e")

        # 2. Info panel (track title, codec, progress slider)
        self.info_panel = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=8)
        self.info_panel.grid(row=1, column=0, sticky="ew", padx=14, pady=6)
        self.info_panel.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(
            self.info_panel, text="No track loaded",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=TEXT_MAIN, anchor="w", justify="left",
            wraplength=700
        )
        self.lbl_title.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 2))

        self.lbl_info = ctk.CTkLabel(
            self.info_panel, text="Select files or a folder to start playback",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_DIM, anchor="w"
        )
        self.lbl_info.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))

        time_frame = ctk.CTkFrame(self.info_panel, fg_color="transparent")
        time_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))
        time_frame.grid_columnconfigure(1, weight=1)

        self.lbl_current_time = ctk.CTkLabel(
            time_frame, text="00:00", width=48,
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM
        )
        self.lbl_current_time.grid(row=0, column=0, sticky="w")

        self.slider_time = ctk.CTkSlider(
            time_frame, from_=0, to=100,
            fg_color=BG_MAIN, progress_color=ACCENT,
            button_color=TEXT_MAIN, button_hover_color=ACCENT_HOVER,
            command=self._on_slider_move
        )
        self.slider_time.set(0)
        self.slider_time.grid(row=0, column=1, sticky="ew", padx=8)
        self.slider_time.bind("<ButtonPress-1>", self._on_drag_start)
        self.slider_time.bind("<ButtonRelease-1>", self._on_drag_end)

        self.lbl_total_time = ctk.CTkLabel(
            time_frame, text="00:00", width=48,
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM
        )
        self.lbl_total_time.grid(row=0, column=2, sticky="e")

        # 3. Control panel
        control_panel = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=8)
        control_panel.grid(row=2, column=0, sticky="ew", padx=14, pady=6)
        control_panel.grid_columnconfigure(1, weight=1)

        buttons_frame = ctk.CTkFrame(control_panel, fg_color="transparent")
        buttons_frame.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.btn_prev = ctk.CTkButton(
            buttons_frame, text="⏮ Prev", width=82, fg_color=BG_ITEM,
            hover_color=BG_ITEM_ACTIVE, text_color=TEXT_MAIN,
            command=self.previous_track
        )
        self.btn_prev.pack(side="left", padx=3)

        self.btn_play = ctk.CTkButton(
            buttons_frame, text="▶ Play", width=110, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color="#0D1A10",
            font=ctk.CTkFont(weight="bold"),
            command=self.toggle_play_pause
        )
        self.btn_play.pack(side="left", padx=3)

        self.btn_stop = ctk.CTkButton(
            buttons_frame, text="⏹ Stop", width=70, fg_color=BG_ITEM,
            hover_color=BG_ITEM_ACTIVE, text_color=TEXT_MAIN,
            command=self.stop
        )
        self.btn_stop.pack(side="left", padx=3)

        self.btn_next = ctk.CTkButton(
            buttons_frame, text="Next ⏭", width=82, fg_color=BG_ITEM,
            hover_color=BG_ITEM_ACTIVE, text_color=TEXT_MAIN,
            command=self.next_track
        )
        self.btn_next.pack(side="left", padx=3)

        volume_frame = ctk.CTkFrame(control_panel, fg_color="transparent")
        volume_frame.grid(row=0, column=2, padx=12, pady=10, sticky="e")

        lbl_vol = ctk.CTkLabel(
            volume_frame, text="🔊",
            font=ctk.CTkFont(size=14), text_color=TEXT_DIM
        )
        lbl_vol.pack(side="left", padx=(0, 4))

        self.slider_volume = ctk.CTkSlider(
            volume_frame, from_=0, to=100, width=90,
            fg_color=BG_MAIN, progress_color=ACCENT,
            button_color=TEXT_MAIN, button_hover_color=ACCENT_HOVER,
            command=self._on_volume_change
        )
        self.slider_volume.set(80)
        self.slider_volume.pack(side="left")

        # 4. Main bottom area split into LEFT (Playlist) and RIGHT (Spectrum)
        bottom_container = ctk.CTkFrame(self, fg_color="transparent")
        bottom_container.grid(row=3, column=0, sticky="nsew", padx=14, pady=(6, 14))
        bottom_container.grid_columnconfigure(0, weight=1)
        bottom_container.grid_columnconfigure(1, weight=0)
        bottom_container.grid_rowconfigure(0, weight=1)

        # LEFT: Track list (Playlist)
        self.playlist_frame = ctk.CTkScrollableFrame(
            bottom_container, fg_color=BG_PANEL, corner_radius=8,
            scrollbar_button_color=BG_ITEM, scrollbar_button_hover_color=BG_ITEM_ACTIVE
        )
        self.playlist_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.playlist_frame.grid_columnconfigure(0, weight=1)
        self.playlist_buttons = []

        # RIGHT: Separated vertical spectrum bars
        right_box = ctk.CTkFrame(bottom_container, fg_color=BG_PANEL, corner_radius=8, width=210)
        right_box.grid(row=0, column=1, sticky="ns")
        right_box.grid_propagate(False)
        right_box.grid_columnconfigure(0, weight=1)
        right_box.grid_rowconfigure(1, weight=1)

        lbl_spec_title = ctk.CTkLabel(
            right_box, text="SPECTRUM",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TEXT_DIM
        )
        lbl_spec_title.grid(row=0, column=0, pady=(8, 4))

        bars_container = ctk.CTkFrame(right_box, fg_color=SPEC_BG, corner_radius=6)
        bars_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        bars_container.grid_rowconfigure(0, weight=1)

        self.spec_bars.clear()
        for i in range(self.num_bars):
            bars_container.grid_columnconfigure(i, weight=1)
            bar = ctk.CTkProgressBar(
                bars_container,
                orientation="vertical",
                width=9,
                corner_radius=3,
                fg_color="#1D2B22",
                progress_color=SPEC_COLOR_LOW
            )
            bar.set(0.0)
            bar.grid(row=0, column=i, sticky="ns", padx=2, pady=8)
            self.spec_bars.append(bar)

    def _compute_spectrum(self, mono_data):
        n_in = len(mono_data)
        if n_in == 0:
            return

        with self.spec_lock:
            if n_in >= self.fft_size:
                self.audio_ring[:] = mono_data[-self.fft_size:]
            else:
                self.audio_ring[:-n_in] = self.audio_ring[n_in:]
                self.audio_ring[-n_in:] = mono_data
            samples = self.audio_ring.copy()

        fft_mag = np.abs(np.fft.rfft(samples * self._fft_window)) / self.fft_size
        fft_mag = fft_mag[2:]

        indices = np.geomspace(2, len(fft_mag), self.num_bars + 1, dtype=int)
        bands = np.zeros(self.num_bars, dtype=np.float32)
        for i in range(self.num_bars):
            start = indices[i]
            end = max(start + 1, indices[i + 1])
            value = np.mean(fft_mag[start:end])
            compensation = 1.0 + (i / self.num_bars) * 2.4
            db = np.log10(1.0 + value * 60.0 * compensation)
            bands[i] = float(np.clip(db, 0.0, 1.0))

        with self.spec_lock:
            self.spec_target = self.spec_target * 0.72 + bands * 0.28

    def _draw_spectrum(self):
        try:
            with self.spec_lock:
                target = self.spec_target.copy()
                self.spec_target *= 0.92

            if not self.is_playing or self.is_paused:
                target = np.zeros(self.num_bars, dtype=np.float32)

            for i in range(self.num_bars):
                diff = target[i] - self.spec_current[i]
                if diff > 0:
                    self.spec_current[i] += diff * 0.32
                else:
                    self.spec_current[i] = max(0.0, self.spec_current[i] + diff * 0.16)

                level = float(self.spec_current[i])
                bar = self.spec_bars[i]
                bar.set(level)

                if level > 0.80:
                    bar.configure(progress_color=SPEC_COLOR_HIGH)
                elif level > 0.50:
                    bar.configure(progress_color=SPEC_COLOR_MID)
                else:
                    bar.configure(progress_color=SPEC_COLOR_LOW)
        except Exception:
            pass
        finally:
            self.after(33, self._draw_spectrum)

    def _on_resize(self, event):
        if event.widget == self:
            new_width = max(300, event.width - 60)
            self.lbl_title.configure(wraplength=new_width)

    def select_files(self):
        paths = filedialog.askopenfilenames(
            title="Select audio or video files"
        )
        if paths:
            valid = [p for p in paths if p.lower().endswith(SUPPORTED_EXTENSIONS)]
            if valid:
                should_play = not self.is_playing
                self.add_to_playlist(valid, play_first=should_play)

    def select_folder(self):
        folder = filedialog.askdirectory(title="Select music folder")
        if folder:
            found = []
            for root, _, files in os.walk(folder):
                for f in sorted(files):
                    if f.lower().endswith(SUPPORTED_EXTENSIONS):
                        found.append(os.path.join(root, f))
            if found:
                should_play = not self.is_playing
                self.add_to_playlist(found, play_first=should_play)

    def add_to_playlist(self, paths, play_first=False):
        first_new_index = len(self.playlist)
        for path in paths:
            if os.path.isfile(path) and path.lower().endswith(SUPPORTED_EXTENSIONS):
                self.playlist.append(path)
        self._redraw_playlist()
        if play_first and len(self.playlist) > first_new_index:
            self.play_index(first_new_index)

    def clear_playlist(self):
        self.stop()
        self.playlist.clear()
        self.current_index = -1
        self._redraw_playlist()
        self.lbl_title.configure(text="No track loaded")
        self.lbl_info.configure(text="Playlist is empty")

    def _redraw_playlist(self):
        for btn in self.playlist_buttons:
            btn.destroy()
        self.playlist_buttons.clear()

        for idx, path in enumerate(self.playlist):
            name = os.path.basename(path)
            active = (idx == self.current_index)
            color = BG_ITEM_ACTIVE if active else BG_ITEM
            prefix = "▶ " if active else f"{idx + 1}. "

            btn = ctk.CTkButton(
                self.playlist_frame,
                text=f"{prefix}{name}",
                anchor="w",
                fg_color=color,
                hover_color=BG_ITEM_ACTIVE,
                text_color=TEXT_MAIN,
                height=32,
                command=lambda i=idx: self.play_index(i)
            )
            btn.grid(row=idx, column=0, sticky="ew", padx=4, pady=2)
            self.playlist_buttons.append(btn)

    def play_index(self, index):
        if not (0 <= index < len(self.playlist)):
            return
        self.stop()
        self._track_finished = False
        self.current_index = index
        self.is_paused = False
        self.stop_event.clear()
        self._redraw_playlist()

        path = self.playlist[index]
        self.lbl_title.configure(text=os.path.basename(path))
        self.btn_play.configure(text="⏸ Pause")

        self.audio_thread = threading.Thread(
            target=self._audio_loop, args=(path,), daemon=True
        )
        self.audio_thread.start()

    def _audio_loop(self, path):
        self.is_playing = True
        finished_naturally = False
        try:
            container = av.open(path)
            stream = container.streams.audio[0]
            sample_rate = stream.rate

            if stream.duration and stream.time_base:
                self.total_duration = float(stream.duration * stream.time_base)
            elif container.duration:
                self.total_duration = float(container.duration / av.time_base)
            else:
                self.total_duration = 0.0

            codec_name = stream.codec_context.name.upper()

            bitrate_bps = stream.bit_rate or container.bit_rate
            if not bitrate_bps and self.total_duration > 0:
                bitrate_bps = int((os.path.getsize(path) * 8) / self.total_duration)
            bitrate_text = f"  |  {round(bitrate_bps / 1000)} kbps" if bitrate_bps else ""

            num_channels = stream.channels
            if num_channels == 1:
                channels_text = "Mono"
            elif num_channels == 2:
                channels_text = "Stereo"
            else:
                channels_text = f"{num_channels} channels"

            base_info = f"{codec_name}  |  {sample_rate} Hz{bitrate_text}"
            self.after(0, lambda: self.lbl_info.configure(
                text=f"{base_info}  |  {channels_text}"
            ))

            # Verify Dual Mono (2-channel file with identical L and R signals)
            dual_mono_check_done = (num_channels != 2)
            checked_blocks = 0

            resampler = av.AudioResampler(format="flt", layout="stereo", rate=sample_rate)
            self.current_time = 0.0

            with sd.OutputStream(samplerate=sample_rate, channels=2, dtype="float32") as out:
                while not self.stop_event.is_set():
                    if self.seek_requested_time is not None:
                        target = self.seek_requested_time
                        self.seek_requested_time = None
                        if stream.time_base:
                            ts = int(target / float(stream.time_base))
                            container.seek(ts, stream=stream, any_frame=False, backward=True)
                            resampler = av.AudioResampler(format="flt", layout="stereo", rate=sample_rate)
                            self.current_time = target

                    if self.is_paused:
                        time.sleep(0.05)
                        continue

                    try:
                        packet = next(container.demux(stream))
                    except StopIteration:
                        finished_naturally = True
                        break

                    if packet.dts is None:
                        continue

                    for frame in packet.decode():
                        if self.stop_event.is_set():
                            break
                        while self.is_paused and not self.stop_event.is_set():
                            time.sleep(0.05)

                        if frame.pts is not None and stream.time_base:
                            self.current_time = float(frame.pts * stream.time_base)

                        for resampled in resampler.resample(frame):
                            if self.stop_event.is_set():
                                break
                            data = np.ascontiguousarray(
                                resampled.to_ndarray().reshape(-1, 2), dtype=np.float32
                            )

                            # Analyze whether 2-channel audio is actually Dual Mono
                            if not dual_mono_check_done:
                                signal_level = np.max(np.abs(data))
                                if signal_level > 0.01:
                                    are_equal = np.allclose(data[:, 0], data[:, 1], atol=1e-4)
                                    if not are_equal:
                                        dual_mono_check_done = True
                                        self.after(0, lambda info=base_info: self.lbl_info.configure(
                                            text=f"{info}  |  Stereo"
                                        ))
                                    else:
                                        checked_blocks += 1
                                        if checked_blocks >= 15:
                                            dual_mono_check_done = True
                                            self.after(0, lambda info=base_info: self.lbl_info.configure(
                                                text=f"{info}  |  Dual Mono"
                                            ))

                            data *= self.volume
                            self._compute_spectrum(data.mean(axis=1))
                            out.write(data)

            container.close()
        except Exception as e:
            self.after(0, lambda err=str(e): self.lbl_info.configure(text=f"Playback error: {err}"))
        finally:
            self.is_playing = False
            with self.spec_lock:
                self.spec_target = np.zeros(self.num_bars, dtype=np.float32)
            if finished_naturally and not self.stop_event.is_set():
                self._track_finished = True

    def toggle_play_pause(self):
        if not self.playlist:
            self.select_files()
            return
        if not self.is_playing:
            idx = self.current_index if self.current_index >= 0 else 0
            self.play_index(idx)
            return

        self.is_paused = not self.is_paused
        self.btn_play.configure(text="▶ Resume" if self.is_paused else "⏸ Pause")

    def stop(self):
        self.stop_event.set()
        self._track_finished = False
        self.is_paused = False
        if self.audio_thread and self.audio_thread.is_alive():
            self.audio_thread.join(timeout=0.5)
        self.is_playing = False
        self.current_time = 0.0
        with self.spec_lock:
            self.spec_target = np.zeros(self.num_bars, dtype=np.float32)
            self.audio_ring.fill(0.0)
        self.btn_play.configure(text="▶ Play")
        self.slider_time.set(0)
        self.lbl_current_time.configure(text="00:00")

    def next_track(self):
        if not self.playlist:
            return
        new_idx = self.current_index + 1
        if new_idx < len(self.playlist):
            self.play_index(new_idx)
        else:
            self.stop()

    def previous_track(self):
        if not self.playlist:
            return
        if self.current_time > 3.0:
            self.seek_requested_time = 0.0
        else:
            new_idx = max(0, self.current_index - 1)
            self.play_index(new_idx)

    def _on_volume_change(self, value):
        self.volume = float(value) / 100.0

    def _on_drag_start(self, _event):
        self.slider_dragging = True

    def _on_slider_move(self, value):
        if self.slider_dragging and self.total_duration > 0:
            target = (float(value) / 100.0) * self.total_duration
            self.lbl_current_time.configure(text=self._format_time(target))

    def _on_drag_end(self, _event):
        if self.total_duration > 0 and self.is_playing:
            percentage = self.slider_time.get() / 100.0
            self.seek_requested_time = percentage * self.total_duration
        self.slider_dragging = False

    def _update_ui_loop(self):
        if self._track_finished:
            self._track_finished = False
            self.next_track()

        if self.is_playing and not self.slider_dragging and self.total_duration > 0:
            percentage = min(100.0, (self.current_time / self.total_duration) * 100.0)
            self.slider_time.set(percentage)
            self.lbl_current_time.configure(text=self._format_time(self.current_time))
            self.lbl_total_time.configure(text=self._format_time(self.total_duration))
        self.after(150, self._update_ui_loop)

    @staticmethod
    def _format_time(seconds):
        seconds = max(0, int(seconds))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    def _on_close(self):
        self.stop()
        self.destroy()


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    input_files = [arg for arg in sys.argv[1:] if os.path.exists(arg)]
    app = AudioPlayer(initial_files=input_files)
    app.mainloop()

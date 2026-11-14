import os
import sys
import threading
import time
import tkinter as tk
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

# Colors for the small spectrum analyzer on the right
SPEC_BG = "#141D17"
SPEC_GRID = "#1E2D23"
SPEC_COLOR_LOW = "#4CAF50"     # Green (bottom)
SPEC_COLOR_MID = "#CDDC39"     # Yellow-green (middle)
SPEC_COLOR_HIGH = "#FF7043"    # Orange-red (peaks)
SPEC_PEAK = "#FFFFFF"

SUPPORTED_EXTENSIONS = (
    ".mp3", ".flac", ".m4a", ".wav", ".alac", ".ogg", ".aac", ".wma",
    ".aiff", ".aif", ".opus", ".wv", ".ape", ".mka", ".ac3", ".dts",
    ".dsf", ".dff", ".mp4", ".mkv", ".mov", ".webm"
)


class AudioPlayer(ctk.CTk):
    def __init__(self, initial_files=None):
        super().__init__()

        self.title("Audio Player")
        self.geometry("760x500")
        self.minsize(540, 380)
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

        # Data for the small 12-band color spectrum analyzer on the right
        self.num_bars = 12
        self.spec_target = np.zeros(self.num_bars, dtype=np.float32)
        self.spec_current = np.zeros(self.num_bars, dtype=np.float32)
        self.spec_peaks = np.zeros(self.num_bars, dtype=np.float32)
        self.spec_lock = threading.Lock()

        self._create_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Configure>", self._on_resize)

        if initial_files:
            self.add_to_playlist(initial_files, play_first=True)

        self.after(200, self._update_ui_loop)
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
            width=120, command=self.select_files
        )
        btn_files.grid(row=0, column=0, padx=(10, 6), pady=10, sticky="w")

        btn_folder = ctk.CTkButton(
            top_panel, text="Open Folder", fg_color=BG_ITEM,
            hover_color=BG_ITEM_ACTIVE, text_color=TEXT_MAIN,
            width=130, command=self.select_folder
        )
        btn_folder.grid(row=0, column=1, padx=6, pady=10, sticky="w")

        btn_clear = ctk.CTkButton(
            top_panel, text="Clear", fg_color="transparent",
            border_width=1, border_color=BG_ITEM_ACTIVE,
            hover_color=BG_ITEM, text_color=TEXT_DIM,
            width=80, command=self.clear_playlist
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
            wraplength=680
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
            buttons_frame, text="⏮", width=38, fg_color=BG_ITEM,
            hover_color=BG_ITEM_ACTIVE, text_color=TEXT_MAIN,
            command=self.previous_track
        )
        self.btn_prev.pack(side="left", padx=3)

        self.btn_play = ctk.CTkButton(
            buttons_frame, text="▶ Play", width=96, fg_color=ACCENT,
            hover_color=ACCENT_HOVER, text_color="#0D1A10",
            font=ctk.CTkFont(weight="bold"),
            command=self.toggle_play_pause
        )
        self.btn_play.pack(side="left", padx=3)

        self.btn_stop = ctk.CTkButton(
            buttons_frame, text="⏹", width=38, fg_color=BG_ITEM,
            hover_color=BG_ITEM_ACTIVE, text_color=TEXT_MAIN,
            command=self.stop
        )
        self.btn_stop.pack(side="left", padx=3)

        self.btn_next = ctk.CTkButton(
            buttons_frame, text="⏭", width=38, fg_color=BG_ITEM,
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
            volume_frame, from_=0, to=100, width=95,
            fg_color=BG_MAIN, progress_color=ACCENT,
            button_color=TEXT_MAIN, button_hover_color=ACCENT_HOVER,
            command=self._on_volume_change
        )
        self.slider_volume.set(80)
        self.slider_volume.pack(side="left")

        # 4. Main bottom area split into LEFT (Playlist) and RIGHT (small spectrum analyzer)
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

        # RIGHT: Small window for the bar analyzer
        right_box = ctk.CTkFrame(bottom_container, fg_color=BG_PANEL, corner_radius=8, width=180)
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

        self.spec_canvas = tk.Canvas(
            right_box, width=156, bg=SPEC_BG,
            highlightthickness=1, highlightbackground=BG_ITEM
        )
        self.spec_canvas.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

    def _compute_spectrum(self, mono_data):
        n = len(mono_data)
        if n < 64:
            return
        window = np.hanning(n)
        fft_mag = np.abs(np.fft.rfft(mono_data * window)) / n
        fft_mag = fft_mag[1:]
        if len(fft_mag) < self.num_bars:
            return

        indices = np.geomspace(1, len(fft_mag), self.num_bars + 1, dtype=int)
        bands = np.zeros(self.num_bars, dtype=np.float32)
        for i in range(self.num_bars):
            start = indices[i] - 1
            end = max(start + 1, indices[i + 1] - 1)
            value = np.mean(fft_mag[start:end])
            compensation = 1.0 + (i / self.num_bars) * 2.2
            db = np.log10(1.0 + value * 45.0 * compensation)
            bands[i] = float(np.clip(db, 0.0, 1.0))

        with self.spec_lock:
            self.spec_target = np.maximum(self.spec_target, bands)

    def _draw_spectrum(self):
        try:
            width = self.spec_canvas.winfo_width()
            height = self.spec_canvas.winfo_height()
            if width > 20 and height > 20:
                self.spec_canvas.delete("all")

                for gy in range(16, height, 16):
                    self.spec_canvas.create_line(0, gy, width, gy, fill=SPEC_GRID, width=1)

                with self.spec_lock:
                    target = self.spec_target.copy()
                    self.spec_target *= 0.65

                if not self.is_playing or self.is_paused:
                    target = np.zeros(self.num_bars, dtype=np.float32)

                for i in range(self.num_bars):
                    if target[i] > self.spec_current[i]:
                        self.spec_current[i] = target[i]
                    else:
                        self.spec_current[i] = max(0.0, self.spec_current[i] - 0.07)

                    if self.spec_current[i] >= self.spec_peaks[i]:
                        self.spec_peaks[i] = self.spec_current[i]
                    else:
                        self.spec_peaks[i] = max(0.0, self.spec_peaks[i] - 0.025)

                margin = 8
                available_width = width - 2 * margin
                gap = 3
                bar_width = max(4.0, (available_width - (self.num_bars - 1) * gap) / self.num_bars)
                bottom = height - 6
                max_bar_height = height - 14

                for i in range(self.num_bars):
                    x1 = margin + i * (bar_width + gap)
                    x2 = x1 + bar_width
                    level = self.spec_current[i]

                    if level > 0.01:
                        mid_threshold = 0.50
                        high_threshold = 0.80

                        u_low = min(level, mid_threshold)
                        y_low = bottom - u_low * max_bar_height
                        self.spec_canvas.create_rectangle(
                            x1, y_low, x2, bottom, fill=SPEC_COLOR_LOW, outline=""
                        )

                        if level > mid_threshold:
                            u_mid = min(level, high_threshold)
                            y_mid = bottom - u_mid * max_bar_height
                            self.spec_canvas.create_rectangle(
                                x1, y_mid, x2, y_low, fill=SPEC_COLOR_MID, outline=""
                            )

                        if level > high_threshold:
                            y_high = bottom - level * max_bar_height
                            self.spec_canvas.create_rectangle(
                                x1, y_high, x2, y_mid, fill=SPEC_COLOR_HIGH, outline=""
                            )

                    peak = self.spec_peaks[i]
                    if peak > 0.02:
                        py = bottom - peak * max_bar_height - 3
                        self.spec_canvas.create_rectangle(
                            x1, py, x2, py + 2, fill=SPEC_PEAK, outline=""
                        )
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

            # Variables to verify Dual Mono (2-channel file with identical L and R signals)
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
                                # Ignore initial silence before music starts
                                if signal_level > 0.01:
                                    are_equal = np.allclose(data[:, 0], data[:, 1], atol=1e-4)
                                    if not are_equal:
                                        # Difference between L and R detected -> confirmed Stereo
                                        dual_mono_check_done = True
                                        self.after(0, lambda info=base_info: self.lbl_info.configure(
                                            text=f"{info}  |  Stereo"
                                        ))
                                    else:
                                        checked_blocks += 1
                                        if checked_blocks >= 15:
                                            # 15 consecutive audio blocks have identical L and R -> Dual Mono
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
                self.after(100, self.next_track)

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
        self.is_paused = False
        if self.audio_thread and self.audio_thread.is_alive():
            self.audio_thread.join(timeout=0.5)
        self.is_playing = False
        self.current_time = 0.0
        with self.spec_lock:
            self.spec_target = np.zeros(self.num_bars, dtype=np.float32)
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
        if self.is_playing and not self.slider_dragging and self.total_duration > 0:
            percentage = min(100.0, (self.current_time / self.total_duration) * 100.0)
            self.slider_time.set(percentage)
            self.lbl_current_time.configure(text=self._format_time(self.current_time))
            self.lbl_total_time.configure(text=self._format_time(self.total_duration))
        self.after(200, self._update_ui_loop)

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
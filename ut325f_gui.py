import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import serial
import serial.tools.list_ports
import threading
import struct
import time
import datetime
import csv
import numpy as np
import matplotlib
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

matplotlib.use("TkAgg")

FRAME_HEADER = b'\xAA\x55\x00\x34\x01'
FRAME_SIZE = 5 + 20 + 20 + 8

DEFAULT_LABELS = ["Channel 1", "Channel 2", "Channel 3", "Channel 4"]
COLOR_OPTIONS = [
    ('red', '#ff0000'),
    ('blue', '#0000ff'),
    ('green', '#008000'),
    ('orange', '#ffa500'),
    ('purple', '#800080'),   # Channel 4 default
    ('magenta', '#ff00ff'),
    ('cyan', '#00ffff'),
    ('black', '#000000'),
    ('brown', '#a52a2a'),
    ('navy', '#000080'),
    ('olive', '#808000'),
    ('teal', '#008080')
]

class ColorLineCombo(ttk.Combobox):
    def __init__(self, master, color_list, init_color, **kwargs):
        self._color_list = color_list
        self._color_names = [name for name, code in color_list]
        self._color_codes = [code for name, code in color_list]
        super().__init__(master, values=self._color_names, state="readonly", **kwargs)
        self.set(init_color)
        self.bind('<Expose>', self._customize_dropdown)
        self.bind('<<ComboboxSelected>>', self._customize_dropdown)
        self.bind('<Button-1>', self._customize_dropdown)

    def _customize_dropdown(self, event=None):
        try:
            menu = self.tk.call("ttk::combobox::PopdownWindow", self, "f.l")
            listbox = self.tk.globalgetvar(menu)
            for i, color in enumerate(self._color_codes):
                self.tk.call(listbox, "itemconfigure", i, "-foreground", color)
        except Exception:
            pass

    def get_color(self):
        idx = self.current()
        if idx < 0:
            idx = 0
        return self._color_codes[idx]

    def set_color(self, color_code):
        for idx, (_, code) in enumerate(self._color_list):
            if code == color_code or code.lower() == color_code.lower():
                self.current(idx)
                break

class TempLoggerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Temperature Logger")
        self.root.geometry("1200x600")
        self.root.resizable(True, True)
        self.root.minsize(1200, 600)

        self.serial_port = None
        self.serial_thread = None
        self.running = False
        self.logging = False
        self.paused = False
        self.stop_event = threading.Event()
        self.is_connected = False

        self.timestamps = []
        self.data = [[] for _ in range(4)]
        self.graph_enabled = [tk.BooleanVar(value=True) for _ in range(4)]
        self.marker_enabled = tk.BooleanVar(value=False)  # Markers disabled by default
        self.y_autoscale = tk.BooleanVar(value=True)
        self.y_min = tk.DoubleVar(value=0.0)
        self.y_max = tk.DoubleVar(value=100.0)
        self.time_window_min = tk.IntVar(value=1)
        self.time_window_sec = tk.IntVar(value=30)
        self.test_start_time = None
        self.channel_colors = [COLOR_OPTIONS[0][1], COLOR_OPTIONS[1][1], COLOR_OPTIONS[2][1], COLOR_OPTIONS[4][1]]
        self.channel_labels = [tk.StringVar(value=DEFAULT_LABELS[i]) for i in range(4)]

        self.build_gui()
        self.refresh_ports()
        self.update_ui()

    def build_gui(self):
        left_frame = ttk.Frame(self.root)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5, pady=5)
        
        port_row = ttk.Frame(left_frame)
        port_row.pack(pady=(2,0), fill=tk.X)
        ttk.Label(port_row, text="Serial Port:").pack(side=tk.LEFT)
        self.port_combo = ttk.Combobox(port_row, width=30, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        self.port_combo.bind("<Button-1>", lambda event: self.refresh_ports())

        # Connect/Disconnect button and status label (centered, directly below dropdown)
        connect_frame = ttk.Frame(left_frame)
        connect_frame.pack(pady=(4, 0), fill=tk.X)
        self.connect_btn = ttk.Button(connect_frame, text="Connect", width=15, command=self.toggle_connection)
        self.connect_btn.pack(pady=(0, 2), padx=2, ipadx=2, ipady=2)
        self.status_label = ttk.Label(connect_frame, text="DISCONNECTED", font=('Arial', 11, 'bold'))
        self.status_label.pack(pady=(0, 2))
        # Add separator after status label
        sep0 = ttk.Separator(left_frame, orient='horizontal')
        sep0.pack(fill='x', pady=(6, 8))

        self.temp_labels = []
        self.color_combos = []
        self.label_entries = []
        temp_frame = ttk.LabelFrame(left_frame)
        temp_frame.pack(fill=tk.X, pady=10)
        temp_title = ttk.Label(temp_frame, text="TEMPERATURES", font=("Arial", 12, "bold"), anchor="center", justify="center")
        temp_title.pack(fill=tk.X, pady=(0, 4))
        for i in range(4):
            row = ttk.Frame(temp_frame)
            row.pack(fill=tk.X, pady=2)
            label_entry = ttk.Entry(row, textvariable=self.channel_labels[i], width=12, font=("Arial", 12))
            label_entry.pack(side=tk.LEFT, padx=(2, 2))
            self.label_entries.append(label_entry)
            label_entry.bind("<FocusOut>", lambda e, idx=i: self.on_label_changed(idx))
            label_entry.bind("<Return>", lambda e, idx=i: self.on_label_changed(idx))
            lbl = ttk.Label(
                row,
                text="--.-- °C",
                font=("Arial", 14, "bold"),
                width=10,
                foreground=self.channel_colors[i],
                anchor="center",
                justify="center"
            )
            lbl.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=False)
            self.temp_labels.append(lbl)
            chk = ttk.Checkbutton(row, text="Enable", variable=self.graph_enabled[i], command=self.update_plot)
            chk.pack(side=tk.LEFT, padx=2)
            color_combo = ColorLineCombo(row, COLOR_OPTIONS, COLOR_OPTIONS[i if i < 3 else 4][0], width=9)
            color_combo.pack(side=tk.LEFT, padx=8)
            color_combo.bind("<<ComboboxSelected>>", lambda e, idx=i: self.on_color_changed(idx))
            self.color_combos.append(color_combo)

        sep = ttk.Separator(left_frame, orient='horizontal')
        sep.pack(fill='x', pady=(8, 4))

        log_label = ttk.Label(left_frame, text="Data Logging/Graphing", font=('Arial', 12, 'bold'))
        log_label.pack(pady=(2, 2))

        # Data logging buttons: Start/Pause, Save, Delete
        log_frame = ttk.Frame(left_frame)
        log_frame.pack(pady=8)
        self.start_btn = ttk.Button(log_frame, text="Start", command=self.start_pause_logging)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.save_btn = ttk.Button(log_frame, text="Save", command=self.save_logging, state=tk.DISABLED)
        self.save_btn.pack(side=tk.LEFT, padx=4)
        self.delete_btn = ttk.Button(log_frame, text="Delete", command=self.delete_logging, state=tk.DISABLED)
        self.delete_btn.pack(side=tk.LEFT, padx=4)
        
        right_frame = ttk.Frame(self.root)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.grid(True, which='both', linestyle='--', linewidth=0.7, alpha=0.7)
        self.lines = []
        for i in range(4):
            line, = self.ax.plot([], [], color=self.channel_colors[i], label=self.channel_labels[i].get(),
                                 linewidth=1, marker='o', markersize=4, markerfacecolor=self.channel_colors[i])
            self.lines.append(line)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Temperature (°C)")
        self.ax.set_xlim(0, 90)
        self.legend = self.ax.legend(loc="upper left", fontsize=10)

        self.canvas = FigureCanvasTkAgg(self.fig, right_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        controls_row1 = ttk.Frame(right_frame)
        controls_row1.pack(fill=tk.X, pady=6)
        ttk.Checkbutton(
            controls_row1, text="Show Markers",
            variable=self.marker_enabled,
            command=self.update_plot
        ).pack(side=tk.TOP, anchor="center")

        controls_row2 = ttk.Frame(right_frame)
        controls_row2.pack(fill=tk.X, pady=6)
        controls_row2_inner = ttk.Frame(controls_row2)
        controls_row2_inner.pack(anchor="center")
        ttk.Label(controls_row2_inner, text="Time window:").pack(side=tk.LEFT, padx=(10, 2))
        ttk.Spinbox(controls_row2_inner, from_=0, to=60, textvariable=self.time_window_min, width=3, command=self.update_plot).pack(side=tk.LEFT)
        ttk.Label(controls_row2_inner, text="min").pack(side=tk.LEFT)
        ttk.Spinbox(controls_row2_inner, from_=1, to=3600, textvariable=self.time_window_sec, width=4, command=self.update_plot).pack(side=tk.LEFT)
        ttk.Label(controls_row2_inner, text="sec").pack(side=tk.LEFT)

        controls_row3 = ttk.Frame(right_frame)
        controls_row3.pack(fill=tk.X, pady=6)
        controls_row3_inner = ttk.Frame(controls_row3)
        controls_row3_inner.pack(anchor="center")
        ttk.Checkbutton(
            controls_row3_inner, text="Auto Y",
            variable=self.y_autoscale,
            command=self.update_plot
        ).pack(side=tk.LEFT, padx=(10, 8))
        ttk.Label(controls_row3_inner, text="Y min:").pack(side=tk.LEFT)
        y_min_spin = ttk.Spinbox(controls_row3_inner, from_=-100, to=200, textvariable=self.y_min, width=5, command=self.update_plot)
        y_min_spin.pack(side=tk.LEFT)
        ttk.Label(controls_row3_inner, text="Y max:").pack(side=tk.LEFT)
        y_max_spin = ttk.Spinbox(controls_row3_inner, from_=-100, to=200, textvariable=self.y_max, width=5, command=self.update_plot)
        y_max_spin.pack(side=tk.LEFT)

        self.y_min_spin = y_min_spin
        self.y_max_spin = y_max_spin

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo['values'] = [port for port in reversed(ports)]
        if ports:
            self.port_combo.current(0)

    def toggle_connection(self):
        if self.serial_port and self.serial_port.is_open:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        port = self.port_combo.get()
        if not port:
            messagebox.showerror("Error", "No port selected.")
            return
        try:
            self.serial_port = serial.Serial(port, 115200, timeout=0.2)
            self.connect_btn.config(text="Disconnect")
            self.is_connected = True
            self.update_status_label()
            self.running = True
            self.stop_event.clear()
            self.serial_thread = threading.Thread(target=self.serial_reader, daemon=True)
            self.serial_thread.start()
        except Exception as e:
            messagebox.showerror("Connection Error", str(e))

    def disconnect(self):
        self.running = False
        self.stop_event.set()
        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None
        self.connect_btn.config(text="Connect")
        self.is_connected = False
        self.update_status_label()
        for lbl in self.temp_labels:
            lbl.config(text="--.-- °C")  # Reset to dashes

    def update_status_label(self):
        if self.is_connected:
            self.status_label.config(text="CONNECTED", foreground="green")
        else:
            self.status_label.config(text="DISCONNECTED", foreground="red")

    def serial_reader(self):
        buffer = b''
        while self.running and self.serial_port and self.serial_port.is_open:
            try:
                buffer += self.serial_port.read(128)
                while True:
                    frame, buffer = self.find_frame(buffer)
                    if frame is None:
                        break
                    # Always update the temperature labels, even if not logging
                    temps = self.parse_frame(frame)
                    self.root.after(0, self.handle_new_data, temps)
            except Exception as e:
                if self.running:
                    print("Serial error:", e)
                break
        self.disconnect()

    def find_frame(self, data):
        start = data.find(FRAME_HEADER)
        if start == -1:
            return None, data
        end = start + FRAME_SIZE
        if len(data) < end:
            return None, data
        frame = data[start:end]
        return frame, data[end:]

    def parse_frame(self, frame):
        temps = []
        fifth_bytes = frame[5+16:5+20]
        for i in range(4):
            offset = 5 + i * 4
            val_bytes = frame[offset:offset+4]
            status = fifth_bytes[i]
            if status == 0x30:
                temps.append(None)
            else:
                try:
                    val, = struct.unpack('<f', val_bytes)
                    temps.append(val)
                except Exception:
                    temps.append(None)
        return temps

    def handle_new_data(self, temps):
        now = time.time()
        # Update the temperature labels regardless of logging state
        for i, val in enumerate(temps):
            color = self.color_combos[i].get_color()
            self.temp_labels[i]['foreground'] = color
            if val is not None:
                self.temp_labels[i]['text'] = f"{val:.2f} °C"
            else:
                self.temp_labels[i]['text'] = "-"

        # Only log data for graph when logging and not paused
        if self.logging and not self.paused:
            if not self.timestamps:
                self.start_time = now
            t = now - self.start_time
            self.timestamps.append(t)
            for ch in range(4):
                self.data[ch].append(temps[ch])
        self.update_plot()

    def update_plot(self):
        window = self.time_window_min.get() * 60 + self.time_window_sec.get()
        for i in range(4):
            self.channel_colors[i] = self.color_combos[i].get_color()
            self.temp_labels[i]['foreground'] = self.channel_colors[i]
        if self.timestamps:
            t0 = self.timestamps[-1]
            tmin = max(0, t0 - window)
            mask = [i for i, t in enumerate(self.timestamps) if t >= tmin]
        else:
            mask = []
        self.ax.clear()
        self.ax.grid(True, which='both', linestyle='--', linewidth=0.7, alpha=0.7)
        plotted_lines = []
        plotted_labels = []
        for ch in range(4):
            if self.graph_enabled[ch].get():
                x = [self.timestamps[i] for i in mask]
                y = [self.data[ch][i] for i in mask]
                label = self.channel_labels[ch].get()
                color = self.channel_colors[ch]
                if self.marker_enabled.get():
                    line, = self.ax.plot(x, y, color=color, marker='o', linewidth=1, markersize=4, label=label)
                else:
                    line, = self.ax.plot(x, y, color=color, linewidth=1, label=label)
                plotted_lines.append(line)
                plotted_labels.append(label)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Temperature (°C)")
        if plotted_lines:
            self.ax.legend(plotted_lines, plotted_labels, loc="upper left", fontsize=10)
        if self.timestamps:
            x_min = max(0, self.timestamps[-1] - window)
            x_max = max(self.timestamps[-1], window)
            self.ax.set_xlim(x_min, x_max)
        else:
            self.ax.set_xlim(0, 90)
        if not self.y_autoscale.get():
            self.ax.set_ylim(self.y_min.get(), self.y_max.get())
        self.canvas.draw()

    def start_pause_logging(self):
        # Start or Pause logic with one button
        if not self.logging:
            # Start pressed
            self.logging = True
            self.paused = False
            self.start_btn.config(text="Pause")
            # Save/Delete only enabled when paused and data exists
            self.save_btn.config(state=tk.DISABLED)
            self.delete_btn.config(state=tk.DISABLED)
            self.timestamps = []
            self.data = [[] for _ in range(4)]
            self.start_time = time.time()
            window = self.time_window_min.get() * 60 + self.time_window_sec.get()
            self.ax.set_xlim(0, window)
            self.test_start_time = datetime.datetime.now()
            self.canvas.draw()
        else:
            # Pause pressed
            self.paused = not self.paused
            if self.paused:
                self.start_btn.config(text="Resume")
            else:
                self.start_btn.config(text="Pause")
            # Save/Delete only enabled when paused and data exists
            data_exists = self.has_data()
            state = tk.NORMAL if self.paused and data_exists else tk.DISABLED
            self.save_btn.config(state=state)
            self.delete_btn.config(state=state)
            # Insert NaN to break the plot lines on pause
            if self.paused and self.timestamps:
                next_time = self.timestamps[-1] + 1e-6
                self.timestamps.append(next_time)
                for ch in range(4):
                    self.data[ch].append(np.nan)
        self.update_plot()

    def has_data(self):
        return any(len(d) > 0 for d in self.data) and len(self.timestamps) > 0

    def save_logging(self):
        if not self.has_data():
            messagebox.showinfo("No data", "No data to save.")
            return

        # Use test start time as the default file name
        if self.test_start_time:
            default_name = self.test_start_time.strftime("%Y-%m-%d_%H-%M-%S") + ".csv"
        else:
            default_name = "temperature_log.csv"

        path = filedialog.asksaveasfilename(
            title="Save CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv")]
        )
        if not path:
            return
        try:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                start_str = self.test_start_time.strftime("%Y-%m-%d %H:%M:%S") if self.test_start_time else ""
                writer.writerow([f"Test Start Time (PC): {start_str}"])
                writer.writerow(["Time (s)"] + [self.channel_labels[ch].get() for ch in range(4)])
                for i in range(len(self.timestamps)):
                    row = [f"{self.timestamps[i]:.6f}"]
                    for ch in range(4):
                        v = self.data[ch][i]
                        row.append("" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.2f}")
                    writer.writerow(row)
            messagebox.showinfo("Saved", f"Data saved to {path}")
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    def delete_logging(self):
        if not self.has_data():
            return
        if messagebox.askyesno("Delete Data", "Are you sure you want to delete all logged data?"):
            self.timestamps = []
            self.data = [[] for _ in range(4)]
            self.logging = False
            self.paused = False
            self.start_btn.config(text="Start")
            self.save_btn.config(state=tk.DISABLED)
            self.delete_btn.config(state=tk.DISABLED)
            self.update_plot()

    def on_color_changed(self, idx):
        color = self.color_combos[idx].get_color()
        self.temp_labels[idx]['foreground'] = color
        self.update_plot()

    def on_label_changed(self, idx):
        self.update_plot()

    def update_ui(self):
        state = tk.NORMAL if not self.y_autoscale.get() else tk.DISABLED
        try:
            self.y_min_spin.config(state=state)
            self.y_max_spin.config(state=state)
        except Exception:
            pass

        # Save and Delete enabled only when paused and data exists
        data_exists = self.has_data()
        if not self.logging:
            self.start_btn.config(state=tk.NORMAL, text="Start")
            self.save_btn.config(state=tk.DISABLED)
            self.delete_btn.config(state=tk.DISABLED)
        else:
            if self.paused:
                self.start_btn.config(state=tk.NORMAL, text="Resume")
                state = tk.NORMAL if data_exists else tk.DISABLED
                self.save_btn.config(state=state)
                self.delete_btn.config(state=state)
            else:
                self.start_btn.config(state=tk.NORMAL, text="Pause")
                self.save_btn.config(state=tk.DISABLED)
                self.delete_btn.config(state=tk.DISABLED)

        # Update status label color
        self.update_status_label()

        self.root.after(500, self.update_ui)

    def on_close(self):
        self.running = False
        self.stop_event.set()
        if self.serial_thread and self.serial_thread.is_alive():
            try:
                self.serial_thread.join(timeout=1.0)
            except Exception:
                pass
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = TempLoggerApp(root)
    root.mainloop()
    
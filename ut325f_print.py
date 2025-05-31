import serial
import struct
from serial.tools.list_ports import comports


FRAME_HEADER = b'\xAA\x55\x00\x34\x01'
FRAME_SIZE = 5 + 20 + 20 + 8  # header + 2x(4 temps + 1 flag/status) + footer

def find_frame(data):
    start = data.find(FRAME_HEADER)
    if start == -1:
        return None, data
    end = start + FRAME_SIZE
    if len(data) < end:
        return None, data
    frame = data[start:end]
    return frame, data[end:]

def parse_frame(frame):
    temps = []
    # 5th group is at offset 21 (5 header + 16 floats)
    fifth_bytes = frame[5+16:5+20]  # 4 bytes
    # Each byte in fifth_bytes is 0x00 or 0x30 (ASCII '0')
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

def main():

    BAUDRATE = 115200
    ports = [port for port in comports()]
    selected_port = ports[-1].device
    ser = serial.Serial(selected_port, BAUDRATE, timeout=0.1)
    buffer = b''
    print(f"Listening on {selected_port} at {BAUDRATE} baud...")
    try:
        while True:
            buffer += ser.read(1024)
            while True:
                frame, buffer = find_frame(buffer)
                if frame is None:
                    break
                temps = parse_frame(frame)
                out = []
                for t in temps:
                    out.append(f"{t:.2f}" if t is not None else "-")
                print("Temperatures:", ", ".join(out))
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        ser.close()

if __name__ == '__main__':
    main()
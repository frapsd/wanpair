"""
wanpair — dual-target network monitor (router + internet).
- Reliable ping with short timeout (detects offline router immediately)
- Real-time ASCII graphs with adaptive scale
- Full UP/DOWN event logging
"""
import subprocess
import time
import os
import re
from collections import deque
from datetime import datetime
import statistics
import winsound  # Windows only

# ===== CONFIG =====
ROUTER_IP = "192.168.1.254"
INTERNET_IP = "8.8.8.8"
INTERVAL = 1
DISCONNECT_THRESHOLD = 3       # failed pings before marking DOWN
PING_TIMEOUT_MS = 800          # single ping timeout (ms); unreachable hosts fail fast
GRAPH_WIDTH = 30               # "─"*30 + " 0─400ms" = 39 chars
GRAPH_HEIGHT = 10
MAX_LATENCY_DISPLAY = 400
INNER_WIDTH = 39               # inner graph box width (both columns)
TOTAL_WIDTH = 85               # 1 + 41 + 1 + 41 + 1 = 85
REPORT_INTERVAL = 600          # 10 minutes
LOG_FILE = "wanpair.log"
# ==================

router_history = deque(maxlen=GRAPH_WIDTH)
internet_history = deque(maxlen=GRAPH_WIDTH)

router_fail_count = 0
internet_fail_count = 0

router_disconnect = False
internet_disconnect = False

router_down_start = None
internet_down_start = None

internet_latencies = []
router_latencies = []

start_time = datetime.now()
last_report_time = time.time()

# Parse latency from ping output: "time=12ms", "tempo=12ms" (Windows IT), "time<1ms"
LATENCY_PATTERN = re.compile(r'[tT]empo?[=<>]?\s*(\d+)', re.IGNORECASE)
LATENCY_FALLBACK = re.compile(r'(\d+)\s*ms')


def ping(host: str):
    """
    Ping with a short timeout — detects offline/unplugged router quickly.
    Returns latency in ms, or None if unreachable.
    """
    is_windows = os.name == "nt"
    count_param = "-n" if is_windows else "-c"
    timeout_param = "-w" if is_windows else "-W"  # -w ms on Windows, -W sec on Linux
    timeout_val = str(PING_TIMEOUT_MS) if is_windows else "1"

    cmd = ["ping", count_param, "1", timeout_param, timeout_val, host]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=2,
            encoding="utf-8",
            errors="replace"
        )
        output = (result.stdout or "") + (result.stderr or "")

        match = LATENCY_PATTERN.search(output)
        if not match:
            match = LATENCY_FALLBACK.search(output)

        if match:
            val = int(match.group(1))
            return float(val) if val > 0 else 0.5  # <1ms → 0.5 for graph display

        return None

    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def draw_graph_lines(history, max_latency=MAX_LATENCY_DISPLAY):
    """Return graph lines (INNER_WIDTH chars) for box rendering."""
    data = list(history)

    if not data:
        grid = [[" "] * GRAPH_WIDTH for _ in range(GRAPH_HEIGHT)]
        lines = [(" " + "".join(row)).ljust(INNER_WIDTH) for row in grid]
        lines.append(("─" * GRAPH_WIDTH + " 0─?ms").ljust(INNER_WIDTH))
        return lines, 50

    data = data[-GRAPH_WIDTH:]
    valid = [v for v in data if v is not None]
    if valid:
        recent_max = max(valid[-20:])
        scale = max(50, min(recent_max * 1.2, max_latency))
    else:
        scale = max_latency

    height = GRAPH_HEIGHT
    levels = []
    for val in data:
        if val is None:
            levels.append(-1)
        else:
            lvl = min(max(1, int((val / scale) * height)), height)
            levels.append(lvl)

    pad = GRAPH_WIDTH - len(levels)
    if pad > 0:
        levels = [-2] * pad + levels

    grid = [[" "] * GRAPH_WIDTH for _ in range(height)]
    for col, lvl in enumerate(levels):
        if lvl == -2:
            continue
        if lvl == -1:
            grid[height - 1][col] = "X"
        else:
            for row in range(height - lvl, height):
                grid[row][col] = "█"

    lines = []
    for row in grid:
        lines.append((" " + "".join(row)).ljust(INNER_WIDTH))
    scale_str = "─" * GRAPH_WIDTH + f" 0─{scale:.0f}ms"
    lines.append(scale_str[:INNER_WIDTH].ljust(INNER_WIDTH))
    return lines, scale


def log_event(text: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {text}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def beep():
    if os.name == "nt":
        try:
            winsound.Beep(1200, 400)
        except Exception:
            pass


def clear():
    os.system("cls" if os.name == "nt" else "clear")


log_event("=== wanpair started ===")

print("wanpair — router & internet monitor")

try:
    while True:
        now = datetime.now()
        timestamp = now.strftime("%H:%M:%S")

        router_latency = ping(ROUTER_IP)
        internet_latency = ping(INTERNET_IP)

        # --- ROUTER ---
        if router_latency is None:
            router_fail_count += 1
            router_history.append(None)

            if router_fail_count >= DISCONNECT_THRESHOLD and not router_disconnect:
                router_disconnect = True
                router_down_start = now
                log_event("ROUTER DOWN - Router offline or cable unplugged")
                beep()
        else:
            router_history.append(router_latency)
            router_latencies.append(router_latency)

            if router_disconnect:
                duration = (now - router_down_start).total_seconds()
                log_event(f"ROUTER UP - Restored after {duration:.1f}s")
            router_disconnect = False
            router_fail_count = 0

        # --- INTERNET ---
        if internet_latency is None:
            internet_fail_count += 1
            internet_history.append(None)

            if internet_fail_count >= DISCONNECT_THRESHOLD and not internet_disconnect:
                internet_disconnect = True
                internet_down_start = now
                log_event("INTERNET DOWN - No connectivity")
                beep()
        else:
            internet_history.append(internet_latency)
            internet_latencies.append(internet_latency)

            if internet_disconnect:
                duration = (now - internet_down_start).total_seconds()
                log_event(f"INTERNET UP - Restored after {duration:.1f}s")
            internet_disconnect = False
            internet_fail_count = 0

        if time.time() - last_report_time >= REPORT_INTERVAL:
            if internet_latencies:
                avg = statistics.mean(internet_latencies)
                log_event(f"REPORT - Internet avg: {avg:.2f} ms")
            if router_latencies:
                avg_r = statistics.mean(router_latencies)
                log_event(f"REPORT - Router avg: {avg_r:.2f} ms")
            last_report_time = time.time()

        clear()

        uptime = (now - start_time).total_seconds()
        uptime_str = f"{int(uptime//3600)}h {int((uptime%3600)//60)}m {int(uptime%60)}s"
        r_status = "🔴 DOWN" if router_disconnect else "🟢 OK  "
        i_status = "🔴 DOWN" if internet_disconnect else "🟢 OK  "
        r_val = f"{router_latency:.1f} ms" if router_latency is not None else "---"
        i_val = f"{internet_latency:.1f} ms" if internet_latency is not None else "---"
        avg_str = f"{statistics.mean(internet_latencies[-100:]):.1f} ms" if internet_latencies else "---"

        r_lines, _ = draw_graph_lines(router_history)
        i_lines, _ = draw_graph_lines(internet_history)
        graph_h = max(len(r_lines), len(i_lines))

        tl, tr, bl, br = "╔", "╗", "╚", "╝"
        hz, vt = "═", "║"
        lt, rt = "╠", "╣"
        tt, bt = "╦", "╩"

        w = TOTAL_WIDTH
        cw = (w - 3) // 2
        inner = INNER_WIDTH

        def fill(s, width):
            return s[:width].ljust(width)

        print()
        print("  " + tl + hz * (w - 2) + tr)
        title = f" WANPAIR  ·  Uptime: {uptime_str}  ·  {timestamp}  ·  CTRL+C "
        print("  " + vt + title.ljust(w - 2)[:w - 2] + vt)
        print("  " + lt + hz * cw + tt + hz * cw + rt)

        r_hdr = f" ROUTER  {ROUTER_IP}  {r_status}"
        i_hdr = f" INTERNET  {INTERNET_IP}  {i_status}"
        hdr_w = cw - 1  # emoji = 2 cols in most terminals
        print("  " + vt + fill(r_hdr, hdr_w) + vt + fill(i_hdr, hdr_w) + vt)

        print("  " + vt + "┌" + "─" * inner + "┐" + vt + "┌" + "─" * inner + "┐" + vt)
        for i in range(graph_h):
            r = (r_lines[i] if i < len(r_lines) else "")[:inner].ljust(inner)
            ix = (i_lines[i] if i < len(i_lines) else "")[:inner].ljust(inner)
            print("  " + vt + "│" + r + "│" + vt + "│" + ix + "│" + vt)
        print("  " + vt + "└" + "─" * inner + "┘" + vt + "└" + "─" * inner + "┘" + vt)

        r_st = f" Last: {r_val} "
        i_st = f" Last: {i_val} "
        print("  " + vt + fill(r_st, cw) + vt + fill(i_st, cw) + vt)

        print("  " + lt + hz * cw + bt + hz * cw + rt)
        foot = f" Avg: {avg_str}  ·  {LOG_FILE} "
        print("  " + vt + fill(foot, w - 2) + vt)
        print("  " + bl + hz * (w - 2) + br)

        time.sleep(INTERVAL)

except KeyboardInterrupt:
    print("\nWriting final report...\n")

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        duration = datetime.now() - start_time
        f.write("\n=== FINAL REPORT ===\n")
        f.write(f"Duration: {duration}\n")

        if internet_latencies:
            f.write(f"Internet - avg: {statistics.mean(internet_latencies):.2f} ms | ")
            f.write(f"Min: {min(internet_latencies):.2f} | Max: {max(internet_latencies):.2f} ms\n")
        if router_latencies:
            f.write(f"Router - avg: {statistics.mean(router_latencies):.2f} ms\n")

    print("Report saved to", LOG_FILE)

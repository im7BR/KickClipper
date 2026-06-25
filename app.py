"""
Kick Clipper — Desktop Application Entry Point
================================================
Launches the FastAPI backend server, opens the dashboard in the default
browser, and creates a system tray icon for managing the app lifecycle.
"""

import multiprocessing
import os
import signal
import sys
import threading
import time
import webbrowser

# Handle PyInstaller frozen executable paths
if getattr(sys, "frozen", False):
    # Running as compiled .exe
    BASE_DIR = os.path.dirname(sys.executable)
    # PyInstaller extracts bundled data to a temp folder
    BUNDLE_DIR = sys._MEIPASS
    
    # Redirect stdout and stderr to a log file next to the executable
    try:
        log_file = os.path.join(BASE_DIR, "clipper.log")
        sys.stdout = open(log_file, "a", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
        print(f"\n--- Application started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    except Exception:
        pass
else:
    # Running as script
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

# Set environment so worker.py can find the bundled dashboard
os.environ["CLIPPER_BASE_DIR"] = BASE_DIR
os.environ["CLIPPER_BUNDLE_DIR"] = BUNDLE_DIR

HOST = "127.0.0.1"
PORT = 8000
DASHBOARD_URL = f"http://{HOST}:{PORT}"


def start_server():
    """Start the uvicorn server in the current thread."""
    import uvicorn
    try:
        if getattr(sys, "frozen", False):
            # In compiled mode, import worker directly so PyInstaller collects it
            from worker import app as fastapi_app
            uvicorn.run(
                fastapi_app,
                host=HOST,
                port=PORT,
                log_level="info",
                reload=False,
            )
        else:
            # In dev mode, use string to allow hot-reloads
            uvicorn.run(
                "worker:app",
                host=HOST,
                port=PORT,
                log_level="info",
                reload=True,
            )
    except Exception as e:
        print(f"Server startup failed: {e}")
        # If running as compiled exe, show a message box error dialog to the user
        if getattr(sys, "frozen", False):
            try:
                import ctypes
                error_msg = str(e)
                if "10048" in error_msg or "address already in use" in error_msg.lower():
                    friendly_msg = "Error: Port 8000 is already in use by another application.\n\n" \
                                   "Please close any other running instances of Kick Clipper or " \
                                   "other applications using port 8000, and try again."
                else:
                    friendly_msg = f"Application failed to start:\n\n{error_msg}\n\n" \
                                   "Check 'clipper.log' for more details."
                
                ctypes.windll.user32.MessageBoxW(
                    0, 
                    friendly_msg, 
                    "Kick Clipper — Startup Error", 
                    0x10  # MB_ICONERROR
                )
            except Exception:
                pass
        os._exit(1)


def wait_for_server(timeout=15):
    """Wait until the server is accepting connections."""
    import socket
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.create_connection((HOST, PORT), timeout=1)
            sock.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.3)
    return False


def open_browser():
    """Open the dashboard in the default browser after server is ready."""
    if wait_for_server():
        webbrowser.open(DASHBOARD_URL)
    else:
        print("Warning: Server did not start in time. Open manually:", DASHBOARD_URL)


def run_with_tray():
    """Run the app with a system tray icon (if pystray is available)."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("pystray/Pillow not available — running without system tray.")
        run_without_tray()
        return

    # Create a simple tray icon (green circle with K)
    def create_icon_image():
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Green circle background
        draw.ellipse([2, 2, size - 2, size - 2], fill=(83, 252, 24, 255))
        # K letter in dark
        try:
            from PIL import ImageFont
            font = ImageFont.truetype("arial.ttf", 32)
        except Exception:
            font = ImageFont.load_default()
        draw.text((size // 2, size // 2), "K", fill=(10, 10, 15, 255),
                   font=font, anchor="mm")
        return img

    server_thread = None
    icon = None

    def on_open(icon_ref, item):
        webbrowser.open(DASHBOARD_URL)

    def on_quit(icon_ref, item):
        icon_ref.stop()
        os._exit(0)

    def on_check_update(icon_ref, item):
        webbrowser.open(f"{DASHBOARD_URL}#check-update")

    # Build the tray menu
    menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", on_open, default=True),
        pystray.MenuItem("Check for Updates", on_check_update),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    # Start the server in a background thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Open browser after a short delay
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    print(f"Kick Clipper is running. Dashboard: {DASHBOARD_URL}")

    try:
        icon = pystray.Icon(
            name="KickClipper",
            icon=create_icon_image(),
            title="Kick Clipper",
            menu=menu,
        )
        print("Look for the tray icon to manage the app.")
        icon.run()
    except Exception as e:
        print(f"System tray failed to initialize: {e}")
        print("Running in background loop (Press Ctrl+C to exit).")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
            sys.exit(0)


def run_without_tray():
    """Fallback: run without system tray (console mode)."""
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    open_browser()

    print(f"\nKick Clipper is running. Dashboard: {DASHBOARD_URL}")
    print("Press Ctrl+C to quit.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


def main():
    # Needed for PyInstaller on Windows
    multiprocessing.freeze_support()

    print("=" * 50)
    print("  Kick Clipper — Starting...")
    print("=" * 50)

    run_with_tray()


if __name__ == "__main__":
    main()

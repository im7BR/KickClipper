"""
Stream Clipper — Standalone Native Desktop App
============================================
Launches the FastAPI backend server, and opens a native desktop window
rendering the Desktop App UI using pywebview.
"""

import multiprocessing
import os
import sys
import threading
import time
import webview

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
                    "Stream Clipper — Startup Error", 
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


class Api:
    def select_folder(self):
        """Open a native folder selection dialog and return the selected path."""
        window = webview.active_window()
        if window:
            result = window.create_file_dialog(webview.FOLDER_DIALOG)
            if result and len(result) > 0:
                return result[0]
        return None


def main():
    # Needed for PyInstaller on Windows
    multiprocessing.freeze_support()

    print("=" * 50)
    print("  Stream Clipper — Starting native window...")
    print("=" * 50)

    # Start FastAPI server in a background thread
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Wait for server to start before launching the window
    if wait_for_server():
        # Open a native desktop GUI window displaying our dashboard served locally
        api = Api()
        webview.create_window(
            title="Stream Clipper",
            url=DASHBOARD_URL,
            width=900,
            height=780,
            resizable=True,
            min_size=(750, 650),
            js_api=api,
        )
        # Start the GUI event loop (this blocks until the window is closed)
        webview.start()
        
        # Once the window is closed, cleanly shutdown the background processes and exit
        print("Window closed. Shutting down application...")
        try:
            import requests
            requests.post(f"http://{HOST}:{PORT}/api/stop", timeout=2)
        except Exception as e:
            print("Failed to stop capture on exit:", e)
            
        time.sleep(0.3)
        sys.exit(0)
    else:
        print("Error: Local backend server failed to start. Exiting.")
        if getattr(sys, "frozen", False):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, 
                    "Error: The backend server did not start in time. The application will close.", 
                    "Stream Clipper — Timeout", 
                    0x10
                )
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()

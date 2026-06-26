import os
import shutil
import subprocess
import sys
from pathlib import Path

def clean_dirs():
    """Remove build artifacts from previous builds."""
    print("Cleaning build and dist directories...")
    for folder in ["build", "dist"]:
        path = Path(folder)
        if path.exists():
            try:
                shutil.rmtree(path)
                print(f"Removed {folder}/")
            except Exception as e:
                print(f"Failed to remove {folder}/: {e}")

def build():
    # Make sure we are in the script's directory
    script_dir = Path(__file__).parent.resolve()
    os.chdir(script_dir)

    clean_dirs()

    # Generate logo.ico if it doesn't exist
    if not Path("logo.ico").exists():
        try:
            from generate_icon import generate_ico
            generate_ico()
        except Exception as e:
            print(f"Warning: Failed to auto-generate logo.ico: {e}")

    # 1. Build updater.exe (console tool to do file swap)
    print("\n--- Building updater.exe ---")
    updater_cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--console",
        "--clean",
        "--name", "updater",
        "--icon", "logo.ico",
        "updater.py"
    ]
    print("Running:", " ".join(updater_cmd))
    subprocess.run(updater_cmd, check=True)

    # 2. Build StreamClipper.exe (main UI/tray/backend)
    print("\n--- Building StreamClipper.exe ---")
    # In Windows PyInstaller, add-data format is "source;dest"
    # dashboard.html will be placed at the root of the temp _MEIPASS folder
    app_cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        "--clean",
        "--name", "StreamClipper",
        "--add-data", "dashboard.html;.",
        "--add-data", "dist/updater.exe;.",
        "--icon", "logo.ico",
        "--version-file", "file_version_info.txt",
        "--collect-all", "curl_cffi",
        "--hidden-import", "_cffi_backend",
        "--collect-all", "imageio_ffmpeg",
        "app.py"
    ]
    print("Running:", " ".join(app_cmd))
    subprocess.run(app_cmd, check=True)

    # Verify output
    dist_dir = script_dir / "dist"
    clipper_exe = dist_dir / "StreamClipper.exe"
    updater_exe = dist_dir / "updater.exe"

    if clipper_exe.exists() and updater_exe.exists():
        print("\n" + "="*60)
        print("SUCCESSFULLY BUILT STREAM CLIPPER DESKTOP APP!")
        print(f"Main Executable: {clipper_exe}")
        print(f"Helper Updater:  {updater_exe}")
        print("Note: Both files must be kept in the same directory for auto-update to work.")
        print("="*60)
    else:
        print("\nERROR: Build completed but one or more files are missing in dist/")

if __name__ == "__main__":
    try:
        build()
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed during PyInstaller execution: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        sys.exit(1)

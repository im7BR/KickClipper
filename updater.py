"""
Kick Clipper — Self-Updater
============================
Small helper that replaces the running .exe with a downloaded update.
This runs as a separate process because Windows locks running executables.

Usage: updater.exe <new_exe_path> <target_exe_path>
"""

import os
import shutil
import subprocess
import sys
import time


def main():
    if len(sys.argv) < 3:
        print("Usage: updater <new_exe_path> <target_exe_path>")
        sys.exit(1)

    new_exe = sys.argv[1]
    target_exe = sys.argv[2]

    print(f"Updater: Replacing {target_exe}")
    print(f"Updater: With {new_exe}")

    # Wait for the old process to release the file lock
    max_wait = 30  # seconds
    waited = 0
    while waited < max_wait:
        try:
            # Try to open the file exclusively to see if it's unlocked
            if os.path.exists(target_exe):
                with open(target_exe, "a"):
                    pass
            break
        except (PermissionError, OSError):
            time.sleep(0.5)
            waited += 0.5
            print(f"Updater: Waiting for old process to exit... ({waited:.0f}s)")

    if waited >= max_wait:
        print("Updater: Timeout waiting for old process. Aborting.")
        sys.exit(1)

    # Create a backup
    backup_path = target_exe + ".bak"
    try:
        if os.path.exists(target_exe):
            shutil.move(target_exe, backup_path)
            print(f"Updater: Backed up old exe to {backup_path}")
    except Exception as e:
        print(f"Updater: Failed to backup: {e}")
        sys.exit(1)

    # Copy the new exe into place
    try:
        shutil.copy2(new_exe, target_exe)
        print(f"Updater: Successfully replaced exe")
    except Exception as e:
        print(f"Updater: Failed to copy new exe: {e}")
        # Restore backup
        if os.path.exists(backup_path):
            shutil.move(backup_path, target_exe)
            print("Updater: Restored backup")
        sys.exit(1)

    # Clean up
    try:
        if os.path.exists(backup_path):
            os.remove(backup_path)
        if os.path.exists(new_exe):
            os.remove(new_exe)
    except Exception:
        pass  # Non-critical cleanup

    # Re-launch the updated exe
    print(f"Updater: Launching updated application...")
    try:
        subprocess.Popen(
            [target_exe],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as e:
        print(f"Updater: Failed to relaunch: {e}")

    print("Updater: Done.")


if __name__ == "__main__":
    main()

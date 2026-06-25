# 🟢 Kick Clipper (Desktop App)

A custom, real-time live stream clipping tool designed for **Kick.com** stream moderators and creators. It runs as a lightweight background worker, maintains a rolling 5-minute HLS stream buffer, and provides a stunning, premium web-based Moderator Dashboard with system tray control and a built-in auto-update system via GitHub.

---

## ✨ Features

- **🔄 Rolling 5-Minute Buffer**: Automatically captures HLS streams in 10-second segments. Discards segments older than 5 minutes to keep disk space usage minimal and prevent memory leaks.
- **⚡ Near-Instant Clipping**: Concat slices without re-encoding (`ffmpeg -c copy`), generating MP4 clips in under 1 second.
- **💻 Desktop App / System Tray**: Run it as a windowless application managed via the Windows system tray. Double-clicking or selecting options from the tray icon opens the UI.
- **💾 Auto-Reconnect**: Remembers the last connected streamer and automatically connects on startup.
- **🚀 One-Click Auto Updates**: Automatically checks for updates on GitHub Releases. Alerts the user via a dashboard banner and handles automatic download, file replacement, and app relaunch.
- **🧪 Modern UI**: Elegant dark-themed web dashboard with glassmorphism design, real-time status tracking, instant clip naming, and download management.

---

## 🛠️ Prerequisites

To run or build Kick Clipper, you need the following dependencies installed on your system:

1. **FFmpeg** (Must be on system PATH)
   - Install via Windows Package Manager: `winget install Gyan.FFmpeg`
   - Or download manually from [ffmpeg.org](https://ffmpeg.org/download.html).
2. **Streamlink** (Must be on system PATH)
   - Install via Windows Package Manager: `winget install Streamlink.Streamlink`
   - Or install via python pip: `pip install streamlink`

---

## 🚀 How to Run (Development)

If running from source code:

1. **Install Python Packages**:
   ```bash
   pip install -r requirements.txt
   ```
2. **Launch the App**:
   ```bash
   python app.py
   ```
   *This starts the local FastAPI server at `http://127.0.0.1:8000`, displays a system tray icon, and opens the Moderator Dashboard in your default browser.*

---

## 📦 How to Build Standalone `.exe`

To package Kick Clipper into a standalone Windows executable:

1. Ensure Python dependencies are installed: `pip install -r requirements.txt`
2. Run the build script:
   ```bash
   python build.py
   ```
3. PyInstaller will compile two files in the `dist/` directory:
   - **`KickClipper.exe`**: The main executable.
   - **`updater.exe`**: Helper program for managing automatic self-updates.

> [!IMPORTANT]
> Keep both `KickClipper.exe` and `updater.exe` in the same folder. The main app launches `updater.exe` to perform the file swap during automatic updates.

---

## ☁️ CI/CD Auto-Releases with GitHub

This project contains a GitHub Actions workflow in [`.github/workflows/release.yml`](.github/workflows/release.yml).

When you want to publish a new update:
1. Increment the version number in [`version.py`](version.py) (e.g., `VERSION = "1.0.1"`).
2. Tag your commit and push it to GitHub:
   ```bash
   git tag v1.0.1
   git push origin v1.0.1
   ```
3. The workflow will automatically compile the code on a Windows environment and attach both `KickClipper.exe` and `updater.exe` to a new release on GitHub.
4. Existing users will see an update banner in their dashboard on the next startup and can update automatically.

---

## 📞 Contact & Support

If you need help setting up the application, want to request features, or would like to get in touch, feel free to reach out:

- **Discord**: `20n.`
- **Instagram**: [@aymen.7br](https://instagram.com/aymen.7br)
- **Email**: [aymen7br@gmail.com](mailto:aymen7br@gmail.com)

Developed with 💚 for the Kick streaming community.

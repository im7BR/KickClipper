# 🟢 Stream Clipper (Desktop App)

A custom, real-time live stream clipping tool designed for **Kick.com** and **TikTok Live** stream viewers and creators. It runs as a fully self-contained desktop application with a native GUI interface, maintaining a rolling 5-minute stream buffer and providing a premium dashboard interface.

---

## ✨ Features

- **🔄 Rolling 5-Minute Buffer**: Automatically captures stream segments in the background. Discards segments older than 5 minutes to keep disk space usage minimal.
- **⚡ Near-Instant Clipping**: Concat slices without re-encoding, generating MP4 clips in under 1 second.
- **💻 Zero Setup Standalone .exe**: Fully bundles all dependencies including FFmpeg. No command-line commands or prerequisites are needed for the end-user.
- **🌐 Direct Platform API Integration**: Uses `curl_cffi` to query the Kick and TikTok APIs directly for live stream HLS URLs — no headless browser or third-party plugins required.
- **📁 Custom Save Location**: Allows the user to select their desired clips folder. Connection is disabled until a folder is selected to prevent lost streams.
- **💾 Auto-Reconnect**: Remembers the last connected streamer and automatically reconnects on startup once a save directory is selected.
- **🚀 One-Click Auto Updates**: Automatically checks for updates on GitHub Releases, displays a change log, and installs the update in a single click.
- **🧪 Premium UI**: Stunning dark-themed glassmorphism interface, custom application icon, real-time status tracking, and download management.

---

## 📥 Download & Install

**For regular users** — just download and run:

1. Go to the [**Releases**](https://github.com/im7BR/KickClipper/releases) page.
2. Download `StreamClipper.exe` and `updater.exe` from the latest release.
3. Place both files in the same folder.
4. Double-click `StreamClipper.exe` — done!

> [!IMPORTANT]
> Keep both `StreamClipper.exe` and `updater.exe` in the same directory. The main app uses `updater.exe` to perform automatic self-updates.

---

## 🛠️ How to Run (Development)

If running from source code:

1. **Install Python Packages**:
   ```bash
   pip install -r requirements.txt
   ```
2. **Generate the Icon** (Optional):
   ```bash
   python generate_icon.py
   ```
3. **Launch the App**:
   ```bash
   python app.py
   ```
   *This starts the local FastAPI backend server, queries the Kick API for live streams, and launches the native webview GUI window.*

---

## 📦 How to Build Standalone `.exe`

To package Kick Clipper into a standalone Windows executable:

1. Ensure Python dependencies are installed: `pip install -r requirements.txt`
2. Run the build script:
   ```bash
   python build.py
   ```
3. PyInstaller will compile two files in the `dist/` directory:
   - **`StreamClipper.exe`**: The main desktop application with its custom icon.
   - **`updater.exe`**: Helper program for managing automatic self-updates.

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
3. The workflow will automatically compile the code on a Windows environment (bundling all dependencies and the custom icon) and attach both `StreamClipper.exe` and `updater.exe` to a new release on GitHub.
4. Users will see a notification and changelog modal in their dashboard on launch and can upgrade automatically.

---

## 🔧 Tech Stack

| Component | Technology |
|-----------|-----------|
| **Backend** | Python, FastAPI, Uvicorn |
| **Stream Capture** | FFmpeg (bundled via `imageio-ffmpeg`) |
| **Kick API Access** | `curl_cffi` (bypasses Cloudflare) |
| **Desktop GUI** | pywebview (native window) |
| **Frontend** | HTML, Tailwind CSS, Vanilla JS |
| **Build System** | PyInstaller |
| **CI/CD** | GitHub Actions |

---

## 📞 Contact & Support

If you need help setting up the application, want to request features, or would like to get in touch, feel free to reach out:

- **Discord**: `20n.`
- **Instagram**: [@aymen.7br](https://instagram.com/aymen.7br)
- **Email**: [aymen7br@gmail.com](mailto:aymen7br@gmail.com)

Developed with 💚 for the streaming community.

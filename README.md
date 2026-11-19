# EasyRecord

A simple, free screen recorder for Windows.

Pick what to record (full screen, one window, or a box you drag), set a quality %, and press
Record or **F12**. The video is saved as an MP4 by itself.

- **F12** - start / stop recording (works even when the app is in the background)
- **F9** - pause / carry on
- **F11** - screenshot
- Can record your microphone and the sound your computer plays.

## What you need

- Windows 10 or 11 (recording uses Windows-only parts).
- Python 3 from <https://www.python.org/downloads/> - tick "Add python.exe to PATH" when installing.
  No extra Python packages are needed to run it.
- **FFmpeg** (the free recording engine). It is **not included** in this repo.
  - `build.bat` always uses the `ffmpeg.exe` in this folder. If it is not there, it downloads the FFmpeg
    "essentials" build from <https://www.gyan.dev/ffmpeg/builds/> and puts it here. You can also put
    any `ffmpeg.exe` you already have here first, and then nothing is downloaded.
  - winget helps only when you run from source: `winget install Gyan.FFmpeg` puts ffmpeg on
    your PATH (open a new terminal afterwards so the PATH change shows up).

  EasyRecord looks for FFmpeg in this order: next to the app, next to the Python program running it,
  then on your PATH.

## Run from source

```
python easyrecord.py
```

## Build the app (one file you can double-click)

Double-click `build.bat` and wait a few minutes. It:

1. downloads `ffmpeg.exe` if it is not there yet,
2. makes a private `.venv` folder here and installs PyInstaller into it (your main Python is not changed),
3. builds `dist\EasyRecord.exe` (FFmpeg is packed inside it).

If the Python that made the `.venv` folder is later removed, `build.bat` makes the folder again by
itself. If a build still stops at "Installing the packing tool", delete the `.venv` folder here and
run `build.bat` again.

Optional installer: install [Inno Setup 6](https://jrsoftware.org/isinfo.php), run `build.bat` first,
then open `installer\EasyRecord.iss` in Inno Setup and press **Compile** (Build > Compile).
You get `installer\EasyRecord-Setup.exe`. Inno Setup does not put `ISCC.exe` on your PATH, so to build
from a terminal, run `ISCC.exe` from the Inno Setup folder with the full path to `EasyRecord.iss`.

To redraw the app icon: `pip install pillow`, then `python make_icon.py`.

## Test

```
python -m unittest discover -s tests
```

The tests check the simple logic (quality numbers, settings, where FFmpeg is found) and scan the
repo for private things like personal paths or keys. They open no window and record nothing.

## Where your files go

- Videos and screenshots: `%USERPROFILE%\Videos\EasyRecord` (the Videos folder inside your user folder,
  even if Windows has moved your Videos library somewhere else), or a folder you pick in the app.
- Settings: `.easyrecord.json` in your user folder (made the first time you run it).
- Error log: `.easyrecord-errors.log` in your user folder (only if something goes wrong).

## Credits and third-party software

- [FFmpeg](https://ffmpeg.org/) does the recording. The Windows builds from gyan.dev are licensed under
  the GNU GPL version 3. FFmpeg is not part of this repo. If you share a built `EasyRecord.exe` or the
  installer, FFmpeg is inside it, so include `installer\FFMPEG-LICENSE.txt` and the GPL text
  `installer\GPL-3.0.txt` with it, plus this project's `LICENSE` (the installer already adds all three).
- [PyInstaller](https://pyinstaller.org/) packs the app (only used by `build.bat`).
- [Inno Setup](https://jrsoftware.org/isinfo.php) makes the optional installer.

Built with [Claude Code](https://claude.com/claude-code).

## License

EasyRecord itself is MIT licensed - see [LICENSE](LICENSE). EasyRecord runs FFmpeg as a separate program;
FFmpeg keeps its own GPL license.

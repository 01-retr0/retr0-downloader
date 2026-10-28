# Retr0 Downloader

A simple desktop app that downloads the video from any web page. Copy the link of a page that has a video, paste it into the app, and get the video in the highest available quality. It works with YouTube, Instagram and almost any other site with a video player. Windows and Linux, no Python or command line needed. Created By Retr0.

Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), which supports thousands of sites.

## A note from me

I made this app for myself. It was very simple, and I used it from the terminal. When my friends saw it, they wanted it too, so I made changes and turned it into a native app for both Windows and Linux.

If you find a bug, or you want support for a specific site, let me know.

## Download

Get the latest version from the **[Releases page](../../releases/latest)**:

| System | File |
|---|---|
| Windows 10 / 11 | `Retr0Downloader.exe` |
| Linux | `Retr0Downloader-linux` |

**Windows:** double-click the file. The first time, Windows shows "Windows protected your PC" because the app is not code-signed. Click **More info**, then **Run anyway**.

**Linux:** open a terminal in the download folder and run:

    chmod +x Retr0Downloader-linux
    ./Retr0Downloader-linux

To add it to your app menu, also download `install_linux.sh` and `icon.png` from this repository into the same folder and run `./install_linux.sh`.

## Antivirus warnings

Some antivirus programs may flag this app as a trojan or a virus. This is a false positive, and here is why: to download videos from sites like YouTube, the app can find and load the cookies of a browser where you are actively signed in. Security software sees a program reading browser cookies and treats it as suspicious, even though that is exactly what this feature is for.

- Nothing is uploaded. Everything stays on your computer.
- The cookies are only passed to the download engine (yt-dlp) so the site sees you as a signed-in user.
- The app is open source. You can read the code in this repository and check it yourself.
- Do not use versions of this app from other sources. If someone modified it, use it with caution. Only download from the Releases page of this repository.

## Features

- Works with almost any site that has a video, not only YouTube
- Highest quality video and audio, merged into one file
- Paste the link of a page, a YouTube playlist, or a `.txt` file with one link per line
- Choose where files are saved, with Browse and Open buttons
- Optional proxy (`host:port` or `socks5://host:port`)
- Browser cookies for sites that refuse the download: sign in to the site in your browser, turn Cookies on, and the app uses your login to get the video. For YouTube, Firefox works best
- Stop button, progress bar and a live log

## Updating

The **Update** button does everything in one click: it installs the newest download engine (yt-dlp), then checks this page for a newer app version (with fresh ffmpeg, Deno and curl_cffi), downloads it, verifies it and restarts itself. Use it when a site stops working.

The app file must be in a folder you can write to, such as Desktop or Downloads, not Program Files.

## Privacy

Everything runs on your computer. Settings are stored in a small file in your user folder (`%APPDATA%\pydownload` on Windows, `~/.config/pydownload` on Linux). The app contacts only the sites you download from, plus GitHub and PyPI when you update. Your cookies and login are never sent anywhere else.

## Responsible use

Only download content you own or have permission to download, and follow the terms of the sites you use.

## Run from source

    pip install -r requirements.txt
    python app.py

Needs Python 3.10 or newer. On Linux also install tkinter: `sudo apt install python3-tk`.

## License

Released under the [GNU General Public License v3.0](LICENSE). You can use, modify and share this app, but if you distribute a modified version you must also release its source code under the same license.

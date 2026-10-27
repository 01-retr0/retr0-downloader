# Retr0 Downloader

A simple desktop app that downloads the video from any web page. Copy the link of a page that has a video, paste it into the app, and get the video in the highest available quality. It works with YouTube, Instagram and almost any other site with a video player. Windows and Linux, no Python or command line needed. Created By Retr0.

Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), which supports thousands of sites.

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

## Features

- Works with almost any site that has a video, not only YouTube
- Highest quality video and audio, merged into one file
- Paste the link of a page, a YouTube playlist, or a `.txt` file with one link per line
- Choose where files are saved, with Browse and Open buttons
- Optional proxy (`host:port` or `socks5://host:port`)
- Browser cookies for sites that refuse the download: log in to the site in your browser, turn Cookies on, and the app uses your login to get the video
- Stop button, progress bar and a live log

## Updating

- **Update app** checks this page for a newer version, downloads it, verifies it and restarts itself.
- **Update packages** installs the newest download engine (yt-dlp) and checks whether a newer app version, with fresh ffmpeg, Deno and curl_cffi, is available. Use it when a site stops working.

The app file must be in a folder you can write to, such as Desktop or Downloads, not Program Files.

## Privacy

Everything runs on your computer. Settings are stored in a small file in your user folder (`%APPDATA%\pydownload` on Windows, `~/.config/pydownload` on Linux). The app contacts only the sites you download from, plus GitHub and PyPI when you update.

## Responsible use

Only download content you own or have permission to download, and follow the terms of the sites you use.

## Run from source

    pip install -r requirements.txt
    python app.py

Needs Python 3.10 or newer. On Linux also install tkinter: `sudo apt install python3-tk`.

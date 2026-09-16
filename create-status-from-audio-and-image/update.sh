#!/bin/bash
# em macos: brew install ffmpeg yt-dlp
# em windows:winget install yt-dlp.yt-dlp
# winget install Gyan.FFmpeg

sudo apt update
sudo apt install ffmpeg python3-pip
python3 -m pip install -U yt-dlp


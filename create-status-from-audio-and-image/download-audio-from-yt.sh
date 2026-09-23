#!/bin/bash

yt-dlp \
  -x \
  --audio-format mp3 \
  --audio-quality 192K \
  -o "audio.%(ext)s" \
  "https://www.youtube.com/watch?v=$1"


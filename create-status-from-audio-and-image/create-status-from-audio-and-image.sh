#!/bin/bash

d=$(date "+%Y-%m-%d %H:%M:%S")
ffmpeg \
  -loop 1 \
  -i $1 \
  -i $2 \
  -t $3 \
  -c:v libx264 \
  -preset medium \
  -tune stillimage \
  -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" \
  -c:a aac \
  -b:a 128k \
  -pix_fmt yuv420p \
  -shortest \
  -movflags +faststart \
  "$d-status.mp4"


#!/bin/bash
# Cloud tools for the agency's portable renderer. Safe to run at the start of every session.
if ! command -v ffmpeg >/dev/null; then apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null; fi
# install for the same Python that runs the renderer; a broken system Pillow has been seen, so replace it
python3 -c "from PIL import Image, ImageDraw, ImageFont, ImageFilter; import numpy" 2>/dev/null || python3 -m pip install --quiet --break-system-packages --ignore-installed pillow numpy
python3 -c "from PIL import Image; import numpy; print('pillow and numpy ok')"
ffmpeg -version | head -1

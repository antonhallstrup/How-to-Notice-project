import os
import time
import json
import threading
import gpiozero
from signal import pause
import pygame

import sys
sys.path.append('/home/anton/aroundsound/e-Paper/RaspberryPi_JetsonNano/python/lib')
from waveshare_epd.epd2in13b_V3 import EPD
from PIL import Image, ImageDraw, ImageFont
import random
import math

# SETUP
## Mapping audio modes to their folders
AUDIO_DIR = {'full': 'full_stream', 'ambient': 'ambient_stream'}
TAG_DIR = 'tags'
DISPLAY_UPDATE_INTERVAL = 10

MODE_BUTTON_PIN = 12
SKIP_BUTTON_PIN = 16
LONG_PRESS_DURATION = 1.5

## Player information
track_index = 0
mode = 'full'
current_tags = []
playback_start_time = None
running = True
display_enabled = True

## E-ink display setup
epd = EPD()
epd.init()
epd.Clear()
width = epd.height
height = epd.width

font = ImageFont.load_default()

## Loading audio files
def get_track_list():
    ambient_dir = os.path.join('audio', AUDIO_DIR['ambient'])
    full_dir = os.path.join('audio', AUDIO_DIR['full'])

    ambient_tracks = set()
    full_tracks = set()

    for f in os.listdir(ambient_dir):
        if f.endswith('_ambient.wav') or f.endswith('_ambient.mp3'):
            base = f.replace('_ambient.wav', '').replace('_ambient.mp3', '')
            ambient_tracks.add(base)

    for f in os.listdir(full_dir):
        if f.endswith('_full.wav') or f.endswith('_full.mp3'):
            base = f.replace('_full.wav', '').replace('_full.mp3', '')
            full_tracks.add(base)

    return sorted(ambient_tracks & full_tracks)

tracks = get_track_list()

# Loading audio label files and match to audio files
def load_tags(track_name, mode):
    tag_dir = os.path.join('audio', AUDIO_DIR[mode])
    tag_path = os.path.join(tag_dir, f"{track_name}_{mode}_tags.json")
    print(f"[DEBUG] Looking for tag file: {os.path.abspath(tag_path)}")
    if not os.path.exists(tag_path):
        return []
    with open(tag_path, 'r') as f:
        data = json.load(f)
        if isinstance(data, dict) and "tags" in data:
            sorted_labels = [tag["label"] for tag in sorted(data["tags"], key=lambda x: x["score"], reverse=True)]
            return [{"time": 0, "tags": sorted_labels}]
        if (
            isinstance(data, list)
            and len(data) > 0
            and isinstance(data[0], dict)
            and "start_sec" in data[0]
            and "tags" in data[0]
        ):
            # Transform each entry to {"time": start_sec, "tags": [sorted label list]}
            converted = []
            for entry in data:
                tag_scores = entry.get("tags", {})
                sorted_labels = sorted(tag_scores, key=lambda k: tag_scores[k], reverse=True)
                converted.append({"time": entry["start_sec"], "tags": sorted_labels})
            return converted
        return data

# Display layout
def update_display(tag_list):
    print(f"update_display called with tags: {tag_list}")

## Mode display
    mode_text = "On" if mode == 'ambient' else "Off"
    title_text = "VOICE CANCELLING"

## Inverting background
    text_img = Image.new('1', (height, width), 0)
    text_draw = ImageDraw.Draw(text_img)

## Creating containers for the audio labels
    padding_x = 6
    padding_y = 2
    title_bbox = text_draw.textbbox((0, 0), title_text, font=font)
    title_w = title_bbox[2] - title_bbox[0]
    title_h = title_bbox[3] - title_bbox[1]

    mode_bbox = text_draw.textbbox((0, 0), mode_text, font=font)
    mode_w = mode_bbox[2] - mode_bbox[0]
    mode_h = mode_bbox[3] - mode_bbox[1]

    ### Drawing boxes for each label
    box_width = max(title_w, mode_w) + 2 * padding_x
    box_height = title_h + mode_h + 3 * padding_y

    box_x = (height - box_width) // 2
    box_y = width - box_height - 5

    text_draw.rectangle([box_x, box_y, box_x + box_width, box_y + box_height], outline=255)

    ### Drawing label text centered horizontally inside the box
    title_x = box_x + (box_width - title_w) // 2
    title_y = box_y + padding_y
    text_draw.text((title_x, title_y), title_text, font=font, fill=255)

    mode_x = box_x + (box_width - mode_w) // 2
    mode_y = title_y + title_h + padding_y
    text_draw.text((mode_x, mode_y), mode_text, font=font, fill=255)

    max_width = 120
    padding = 6
    corner_radius = 10
    vertical_margin = 8

    ### Stacking the boxes
    current_y = vertical_margin

    for tag in tag_list:
        ### Wrap the tag text into lines to fit inside the box
        words = tag.split()
        lines = []
        current_line = ""
        for word in words:
            test_line = current_line + (" " if current_line else "") + word
            test_bbox = text_draw.textbbox((0, 0), test_line, font=font)
            test_width = test_bbox[2] - test_bbox[0]
            if test_width <= max_width - 2 * padding:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)

        if len(lines) == 0:
            lines = [tag]

        ### Calculating the bounding box size for the wrapped text
        line_heights = []
        max_line_width = 0
        for line in lines:
            bbox = text_draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            line_heights.append(h)
            if w > max_line_width:
                max_line_width = w
        total_text_height = sum(line_heights) + (len(lines) - 1) * 2

        box_w = max_line_width + 2 * padding
        box_h = total_text_height + 2 * padding

        if current_y + box_h > box_y - vertical_margin:
            break

        ### Centering box horizontally
        x1 = (height - box_w) // 2
        y1 = current_y
        x2 = x1 + box_w
        y2 = y1 + box_h

        candidate_rect = (x1, y1, x2, y2)

        ### Drawing box in white
        text_draw.rounded_rectangle(candidate_rect, radius=corner_radius, fill=255, outline=255)

        ### Drawing label text wrapped in black
        start_y = y1 + padding + (box_h - 2 * padding - total_text_height) // 2
        for i, line in enumerate(lines):
            bbox = text_draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            text_x = x1 + (box_w - text_w) // 2
            text_y = start_y
            text_draw.text((text_x, text_y), line, font=font, fill=0)
            start_y += text_h + 2

        current_y = y2 + vertical_margin

    ## Rotating to fit vertical display
    rotated_text_img = text_img.rotate(-90, expand=True)

    ## Final adjustments
    image = Image.new('1', (width, height), 0)
    image.paste(rotated_text_img, (0, 0))

    white_bg = Image.new('1', (width, height), 1)

    epd.init()
    epd.display(epd.getbuffer(image), epd.getbuffer(white_bg))

# Audio player
def play_track(track_name, mode, start_time=0):
    global current_tags, playback_start_time

    suffix = '_full.wav' if mode == 'full' else '_ambient.wav'
    fallback = suffix.replace('.wav', '.mp3')
    dir_path = os.path.join('audio', AUDIO_DIR[mode])
    audio_path = os.path.join(dir_path, track_name + suffix)

    if not os.path.exists(audio_path):
        audio_path = os.path.join(dir_path, track_name + fallback)

    current_tags = load_tags(track_name, mode)

    ## Full refresh before playing new track, but only if display is enabled
    if display_enabled:
        epd.init()
        epd.Clear()

    pygame.mixer.music.load(audio_path)
    pygame.mixer.music.play(start=start_time)
    playback_start_time = time.time() - start_time

    ## Only update the display if enabled
    def display_worker(tags):
        update_display(tags)
    if display_enabled:
        if current_tags:
            threading.Thread(target=display_worker, args=(current_tags[0]["tags"],), daemon=True).start()
        else:
            threading.Thread(target=display_worker, args=([],), daemon=True).start()


# Making button functions
## Button for voice cancelling mode toggle
def handle_mode_button():
    def short_press():
        global mode
        elapsed = 0
        if playback_start_time is not None:
            elapsed = time.time() - playback_start_time
        mode = 'ambient' if mode == 'full' else 'full'
        print(f"Switched to {mode} mode")
        threading.Timer(0.1, lambda: play_track(tracks[track_index], mode, elapsed)).start()

    ### Disabling display on long press
    def long_press():
        global display_enabled
        display_enabled = not display_enabled
        state = "enabled" if display_enabled else "disabled"
        print(f"Display mode now {state}")

    button_handler(MODE_BUTTON_PIN, short_press, long_press)

## Button for skipping to next audio file
def handle_skip_button():
    def short_press():
        global track_index
        track_index = (track_index + 1) % len(tracks)
        print(f"Skipped to track {track_index + 1}: {tracks[track_index]}")
        threading.Timer(0.1, lambda: play_track(tracks[track_index], mode)).start()

    ### Restarting script on long press
    def long_press():
        epd.init()
        epd.Clear()
        print("Skip button long press detected: restarting script.")
        img = Image.new('1', (width, height), 0)
        draw = ImageDraw.Draw(img)
        small_font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 14) if os.path.exists('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf') else ImageFont.load_default()
        msg = "RESTARTING..."
        bbox = draw.textbbox((0, 0), msg, font=small_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (width - text_w) // 2
        y = (height - text_h) // 2
        draw.text((x, y), msg, font=small_font, fill=255)
        epd.display(epd.getbuffer(img), epd.getbuffer(Image.new('1', (width, height), 1)))
        epd.sleep()
        # Restart the script using execv
        import sys
        import os
        os.execv(sys.executable, ['python3'] + sys.argv)

    button_handler(SKIP_BUTTON_PIN, short_press, long_press)

# Button handler
def button_handler(pin, short_fn, long_fn):
    button = gpiozero.Button(pin, hold_time=LONG_PRESS_DURATION)
    was_held = {'flag': False}

    def on_hold():
        was_held['flag'] = True
        long_fn()

    def on_release():
        if not was_held['flag']:
            if not button.held_time or button.held_time < LONG_PRESS_DURATION:
                short_fn()
        was_held['flag'] = False

    button.when_held = on_hold
    button.when_released = on_release

# Main loop
def main():
    pygame.init()
    pygame.mixer.init()

    handle_mode_button()
    handle_skip_button()

    print("Tracks found:", tracks)
    play_track(tracks[track_index], mode)

    print("Ready. Press buttons to interact.")
    pause()

try:
    main()
except KeyboardInterrupt:
    running = False
    # Clear the e-ink display before exiting
    epd.init()
    epd.Clear()
    print("\nExiting.")
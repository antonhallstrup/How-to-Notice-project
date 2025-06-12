#!/usr/bin/python3
import os
import time
from PIL import Image, ImageDraw, ImageFont
import textwrap
import subprocess
from waveshare_epd import epd2in9_V2
import time
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
import RPi.GPIO as GPIO
import threading
import signal
import sys

# STEP 0: Setup
## E-ink display setup
### Creating status indicator
def show_status(step):
    try:
        epd = epd2in9_V2.EPD()
        epd.init()

        image = Image.new('1', (epd.height, epd.width), 255)
        draw = ImageDraw.Draw(image)

        radius = 7
        spacing = 25
        cx = image.width // 2 - spacing
        cy = image.height // 2

        for i in range(step):
            x = cx + i * spacing
            draw.ellipse((x - radius, cy - radius, x + radius, cy + radius), fill=0)

        rotated = image.rotate(270, expand=True)
        epd.display(epd.getbuffer(rotated))
        time.sleep(1)
    except Exception as e:
        print("Error in show_status:", e)

### Creating empty black and white screens
def show_black_screen():
    try:
        epd = epd2in9_V2.EPD()
        epd.init()

        image = Image.new('1', (epd.height, epd.width), 0)
        epd.display(epd.getbuffer(image.rotate(270, expand=True)))
        time.sleep(1)
    except Exception as e:
        print("Error in show_black_screen:", e)

def show_white_screen():
    try:
        epd = epd2in9_V2.EPD()
        epd.init()

        image = Image.new('1', (epd.height, epd.width), 255)
        epd.display(epd.getbuffer(image.rotate(270, expand=True)))
        time.sleep(1)
    except Exception as e:
        print("Error in show_white_screen:", e)

## Light sensor setup
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
chan_a0 = AnalogIn(ads, ADS.P0)
chan_a1 = AnalogIn(ads, ADS.P1)
LIGHT_THRESHOLD = 9000
DARKNESS_DURATION = 5 

## LED setup
LED_PIN = 18
GPIO.setmode(GPIO.BCM)
GPIO.setup(LED_PIN, GPIO.OUT)
led_pwm = GPIO.PWM(LED_PIN, 500)  # 500 Hz frequency
led_pwm.start(0)

pulsing_event = threading.Event()

### Handle script interruption and cleanup
def handle_exit(signum, frame):
    print("Interrupted, cleaning up GPIO and exiting.")
    pulsing_event.clear()
    led_pwm.stop()
    led_pwm.ChangeDutyCycle(0)
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

### Initialize trigger state variables once at start
photo_triggered = False
a1_prev_above = False

# Main loop
time.sleep(1)

while True:
    ## Shut down in low light
    dark_start = None
    shutdown_screen_shown = False

    while True:
        if chan_a1.value < LIGHT_THRESHOLD and chan_a0.value < LIGHT_THRESHOLD:
            if dark_start is None:
                print("Both sensors are dark, starting shutdown timer...")
                dark_start = time.time()
                show_black_screen()
            elif time.time() - dark_start > 5:
                print("5 seconds of darkness on both sensors - shutting down.")
                show_shutdown_screen()
                os.system("sudo shutdown now")
                ### Reset trigger state for next loop
                photo_triggered = False
                a1_prev_above = False
                break
        else:
            dark_start = None
            break
        time.sleep(0.5)

    photo_triggered = False
    led_pwm.ChangeDutyCycle(0)
    print("LED PWM duty cycle reset to 0. Waiting for light change to trigger photo...")
    
    ## Wait for light to appear (only if A0 is above threshold)
    while not photo_triggered:
        a0_val = chan_a0.value
        a1_val = chan_a1.value

        if a0_val < LIGHT_THRESHOLD:
            # Block trigger if A0 is below threshold
            a1_prev_above = False
        else:
            if a1_val < LIGHT_THRESHOLD:
                a1_prev_above = False
            elif not a1_prev_above and a1_val >= LIGHT_THRESHOLD:
                photo_triggered = True
                a1_prev_above = True
            else:
                a1_prev_above = True

        time.sleep(0.2)

    show_white_screen()
    print("Light detected")

    # STEP 1: Capture image
    led_pwm.ChangeDutyCycle(100)
    time.sleep(0.2)
    image_filename = "latest.jpg"
    print(" ^=^s  Taking photo...")
    subprocess.run(["libcamera-still", "-o", image_filename, "--width", "1920", "--height", "1080", "--nopreview"])

    def pulse_led():
        while pulsing_event.is_set():
            for dc in list(range(0, 101, 5)) + list(range(100, -1, -5)):
                if not pulsing_event.is_set():
                    break
                led_pwm.ChangeDutyCycle(dc)
                print(f"Pulsing LED: duty cycle set to {dc}")
                time.sleep(0.05)

    led_pwm.ChangeDutyCycle(100)
    print("LED turned on to full brightness for photo capture.")
    pulsing_event.set()
    pulse_thread = threading.Thread(target=pulse_led)
    pulse_thread.start()

    show_status(1)

    # STEP 2: Upload image to Cloudinary
    import cloudinary
    import cloudinary.uploader
    from dotenv import load_dotenv

    load_dotenv(os.path.expanduser("~/.cloudinary.env"))

    cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
    )

    print(" ^x^a  ^o Uploading to Cloudinary...")
    upload_result = cloudinary.uploader.upload(image_filename)
    image_url = upload_result["secure_url"]
    print(" ^=^t^w Uploaded to:", image_url)

    show_status(2)

    # STEP 3: Generate description with OpenAI
    from openai import OpenAI

    load_dotenv(os.path.expanduser("~/.openai.env"))

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    print(" Generating description...")

    show_status(3)

    ## OpenAI prompt:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "Observe the photo and notice one single detail in the scene that might be overlooked. Describe it concisely but in vivid detail."
                    "The tone should be reflective, imaginative, and profound, while not overinterpreting the information the photo gives you."
                    "Avoid a general summary of the scene, and instead zoom in on one thing and linger there, as if telling a story with your eyes."
                    "Do not start the description by saying 'In this image...' or referring to the photo in any other way."
                )
            },
            {
                "role": "user",
                "content": "Can you describe something in this image?"
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        }
                    }
                ]
            }
        ],
        temperature=1.0,
        max_tokens=150
    )

    description = response.choices[0].message.content.strip()
    print(description)

    # STEP 4: Display description on e-ink display
    pulsing_event.clear()
    pulse_thread.join()
    print("LED turned off after displaying description.")
    led_pwm.ChangeDutyCycle(0)
    epd = epd2in9_V2.EPD()
    epd.init()
    epd.Clear(0xFF)

    canvas_width = epd.height
    canvas_height = epd.width
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    min_font_size = 8
    max_font_size = 24

    ## Find the font size that fits best based on generated text and fits vertically
    for size in range(max_font_size, min_font_size - 1, -1):
        font = ImageFont.truetype(font_path, size)
        chars_per_line = int(canvas_width / (size * 0.52))
        wrapped = textwrap.fill(description, width=chars_per_line)
        lines = wrapped.split('\n')
        line_heights = [font.getbbox(line)[3] for line in lines]
        total_height = sum(line_heights) + (len(lines) - 1) * 2

        if total_height <= canvas_height:
            break

    font = ImageFont.truetype(font_path, size)
    image = Image.new('1', (canvas_width, canvas_height), 255)
    draw = ImageDraw.Draw(image)

    y = max((canvas_height - total_height) // 2, 0)
    for line in lines:
        draw.text((5, y), line, font=font, fill=0)
        y += font.getbbox(line)[3] + 2

    image = image.rotate(270, expand=True)

    epd.display(epd.getbuffer(image))
    time.sleep(3)
    epd.sleep()


    ## Add description to the uploaded image in Cloudinary
    cloudinary.uploader.explicit(
        public_id=upload_result["public_id"],
        type="upload",
        context={"alt": description}
    )

    print("Done")

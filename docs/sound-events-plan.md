# Plan: count street sounds with a USB microphone (not built)

**Status:** on the list, 2026-10-17. Nothing is built.

## The request

Use a USB microphone to recognise classes of environmental sound around the
sign, such as car horns and people yelling, and count them.

## Is it reasonable on the Pi?

Yes. A pretrained sound classifier does this without training anything:

- **YAMNet** (Google, trained on AudioSet) labels 0.96-second windows of
  16 kHz audio with 521 classes. Among them: *Vehicle horn, car horn,
  honking*; *Shout*; *Yell*; *Crowd*; *Cheering*; *Chant*; *Applause*;
  *Siren*; *Drum*; *Whistle*; *Speech*.
- Its TensorFlow Lite model is small (a few MB) and runs comfortably faster
  than real time on a Pi 4 CPU. Cores 0–2 are free for it; core 3 stays
  reserved for the panel refresh (`isolcpus=3`).
- USB audio does not clash with the matrix driver. The Pi's *onboard* sound
  must stay off, but a USB microphone uses `snd-usb-audio` instead.

## Privacy

At a protest this matters more than accuracy:

- Classify on the device and **keep only counts and timestamps**. Never
  record, store, or transmit audio, and never transcribe speech.
- Audio is held in memory for one window at a time and discarded.
- Say so plainly in the UI and the README, so people near the sign can be
  told what it does.

## Design

1. **Listener.** A background worker reads the USB microphone (ALSA, e.g.
   via `sounddevice`), resamples to 16 kHz mono, and classifies overlapping
   windows.
2. **Events, not frames.** A class counts once per occurrence: it must
   cross a confidence threshold, and a cooldown (e.g. 2 s) stops one long
   honk from counting many times. Thresholds per class, adjustable.
3. **Groups that read well:** for example *Horns* (vehicle horn, car horn,
   truck horn), *Shouting* (shout, yell, screaming), *Crowd* (crowd,
   cheering, chant, applause), *Sirens*, *Drums*.
4. **Storage.** Per-minute counts per group in SQLite, kept for a week,
   like the heat and power record.
5. **UI.** A "Street sounds" card: counts today and in the last hour, a
   24-hour chart per group, and a level meter to check the microphone works.
6. **On the sign (optional).** A message can show a live count, for example
   "HONKS: 1,284", updating as horns are heard.

## Hardware notes

- An outdoor microphone needs a foam or fur windscreen; wind noise
  otherwise swamps the classifier.
- Mount it away from the cooling fans and the power supply, which hum.
- Very loud events clip cheap microphones; a microphone with adjustable
  gain, set lower, copes better with traffic.

## Limits

Counts are estimates. Overlapping sounds, distance, and echoes off buildings
cause misses and doubles, and the classes come from general training data.
Treat the numbers as a lively indicator, not a measurement.

## Effort

About a day for the listener, event counting, storage, and the card; the
live count on the sign adds a little more.

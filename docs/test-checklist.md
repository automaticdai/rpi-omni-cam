# Hardware test checklist

Features added after v0.4.0, to be checked on the camera (Pi Zero 2 W, Camera Module v2,
Display HAT Mini). Ticked items have been tried on the camera and confirmed in the log.

## Done

- [x] Date & time (SETUP › System › Date & time)
- [x] Movie mode: record 1080p video, saved as MP4 with a poster frame
- [x] Play videos on the camera (Y play/pause, B skip 5 s, X stop)
- [x] Phone gallery (SETUP › Wi-Fi & Remote › Phone access On)
- [x] Custom sets: save and recall Custom 1 and 2

## To test

### Photos
- [ ] Ordinary photo, one normal shot (nothing broken)
- [ ] Night mode: Drive › Night · 4 frames (and 8) in dim light; one photo, less grain
- [ ] Double exposure: Drive › Double exposure; first shot shows faintly over the view,
      second shot blends; hold X after the first shot cancels
- [ ] Long exposures: SHOT › Long exposures On; in S or M mode, shutter 2, 4 and 8 s

### Video
- [ ] Slow motion: Mode › S&Q; record, then play back; VID tab at 120 and 200 fps
- [ ] 720p video: VID tab › Video size 720p
- [ ] Timelapse to MP4: Interval 1 s, about 15 frames; "MP4 …%" in the top bar,
      then play it in playback

### Playback
- [ ] Info on photos: Y cycles basic → details with histogram → no overlay

### Phone page
- [ ] Remote tab: live view
- [ ] Remote shutter in a photo mode, and start/stop recording in Movie mode
- [ ] Remote EV − / +
- [ ] Photo details (EXIF table) on the view page
- [ ] Download a photo and a video; delete from the phone

### Controls
- [ ] Left-handed (SETUP › Controls): shutter on the top-left button, hints show the
      right letters
- [ ] Upside down: screen, photos and buttons all rotate

### Wi-Fi (last: these take the Pi off the home network)
- [ ] Wi-Fi Off, then back to Home network
- [ ] Camera hotspot: join it with the Wi-Fi QR code on Connection info, then open the page
- [ ] Auto: falls back to the hotspot when the home network is out of range

Most likely to show hardware-only problems: slow motion at 200 fps, night mode memory
use, and the camera rotation setting (upside down).

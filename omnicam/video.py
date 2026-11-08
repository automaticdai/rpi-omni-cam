"""Video files: wrap recorded H.264 in MP4, and turn timelapse frames into an MP4.

Uses PyAV (python3-av). Recording writes a raw H.264 stream, which has no
timestamps; remuxing gives every frame the same duration, so a slow-motion
clip captured at 120 fps and written at 30 fps plays four times slower.
"""

from __future__ import annotations

import logging
import os
from fractions import Fraction
from pathlib import Path
from typing import Callable, Optional, Sequence

from PIL import Image

log = logging.getLogger(__name__)
MAX_TL_VIDEO = (1920, 1080)  # timelapse videos fit inside this
ENCODERS = ("h264_v4l2m2m", "libx264")  # Pi hardware encoder first


def remux_h264(src: Path, dst: Path, fps: float) -> int:
    """Raw H.264 -> MP4 at a constant `fps`. Returns the number of frames."""
    import av

    tmp = dst.with_name(dst.name + ".part")
    rate = Fraction(fps).limit_denominator(1000)
    frames = 0
    with av.open(str(src), format="h264", options={"framerate": str(float(fps))}) as inp, \
            av.open(str(tmp), "w", format="mp4") as out:
        ist = inp.streams.video[0]
        try:
            ost = out.add_stream_from_template(ist)
        except AttributeError:  # PyAV < 13
            ost = out.add_stream(template=ist)
        ost.time_base = 1 / rate
        for pkt in inp.demux(ist):
            if pkt.size == 0:
                continue
            pkt.stream = ost
            pkt.time_base = 1 / rate
            pkt.pts = pkt.dts = frames
            pkt.duration = 1
            out.mux(pkt)
            frames += 1
    os.replace(tmp, dst)
    return frames


def _even(v: int) -> int:
    return max(2, v - v % 2)


def video_size(img_size: tuple[int, int], limit=MAX_TL_VIDEO) -> tuple[int, int]:
    """Fit img_size inside limit (either orientation), even dimensions."""
    w, h = img_size
    lw, lh = limit if w >= h else limit[::-1]
    scale = min(1.0, lw / w, lh / h)
    return _even(int(w * scale)), _even(int(h * scale))


def frames_to_mp4(frames: Sequence[Path], dst: Path, fps: int,
                  progress: Optional[Callable[[float], None]] = None) -> str:
    """Encode JPEG frames into an H.264 MP4. Returns the encoder used."""
    if not frames:
        raise ValueError("no frames")
    with Image.open(frames[0]) as first:
        size = video_size(first.size)
    last_error: Optional[Exception] = None
    for codec in ENCODERS:
        try:
            _encode(frames, dst, fps, size, codec, progress)
            return codec
        except Exception as e:  # encoder missing or busy: try the next one
            log.warning("timelapse video with %s failed: %s", codec, e)
            last_error = e
            dst.with_name(dst.name + ".part").unlink(missing_ok=True)
    raise RuntimeError(f"no video encoder worked: {last_error}")


def _encode(frames, dst: Path, fps: int, size, codec: str, progress) -> None:
    import av

    tmp = dst.with_name(dst.name + ".part")
    with av.open(str(tmp), "w", format="mp4") as out:
        stream = out.add_stream(codec, rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        stream.bit_rate = 10_000_000
        if codec == "libx264":
            stream.options = {"preset": "veryfast", "crf": "20"}
        for i, path in enumerate(frames):
            with Image.open(path) as im:
                im.draft("RGB", size)  # fast reduced-size JPEG decode
                img = im.convert("RGB")
            if img.size != size:
                img = img.resize(size, Image.BILINEAR)
            for pkt in stream.encode(av.VideoFrame.from_image(img)):
                out.mux(pkt)
            if progress:
                progress((i + 1) / len(frames))
        for pkt in stream.encode():
            out.mux(pkt)
    os.replace(tmp, dst)


def duration_s(path: Path) -> Optional[float]:
    """Length of an MP4 in seconds, or None."""
    try:
        import av

        with av.open(str(path)) as c:
            if c.duration:
                return c.duration / 1e6
            s = c.streams.video[0]
            if s.duration and s.time_base:
                return float(s.duration * s.time_base)
    except Exception:
        pass
    return None


class Player:
    """Plays an MP4 on the screen. A background thread decodes and scales each
    frame with libswscale; frames that are late are decoded but not scaled, so
    playback keeps real time.

    The UI reads `frame`, `position`, `duration`, `playing`. If nothing reads
    the frame for a while (the screen changed), playback pauses by itself.
    """

    IDLE_PAUSE_S = 1.0

    def __init__(self, path: Path, box: tuple[int, int] = (320, 240)):
        import queue
        import threading

        self.path, self.box = Path(path), box
        self.frame: Optional[Image.Image] = None
        self.position = 0.0
        self.duration: Optional[float] = None
        self.playing = False
        self.ended = False
        self.error: Optional[str] = None
        self._cmds: "queue.Queue" = queue.Queue()
        self._seen = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True, name="player")
        self._thread.start()

    # ---- UI thread ----
    def toggle(self) -> None:
        self._cmds.put(("toggle", 0.0))

    def seek(self, delta: float) -> None:
        self._cmds.put(("seek", delta))

    def close(self) -> None:
        self._cmds.put(("close", 0.0))

    def current(self) -> Optional[Image.Image]:
        import time

        self._seen = time.monotonic()
        return self.frame

    # ---- decoding thread ----
    def _run(self) -> None:
        try:
            self._play()
        except Exception as e:  # shown on the screen
            log.warning("playing %s failed: %s", self.path, e)
            self.error = str(e) or type(e).__name__
            self.playing = False

    def _open(self):
        import av

        container = av.open(str(self.path))
        stream = container.streams.video[0]
        if container.duration:
            self.duration = container.duration / 1e6
        elif stream.duration and stream.time_base:
            self.duration = float(stream.duration * stream.time_base)
        w, h = stream.codec_context.width, stream.codec_context.height
        scale = min(self.box[0] / w, self.box[1] / h)
        self._out = (_even(int(w * scale)), _even(int(h * scale)))
        return container, stream

    def _decoder(self, stream):
        """Software decoder on all cores. (The Pi's hardware decoder is faster at
        decoding, but reading its frames back to scale them is ~6x slower.)"""
        ctx = stream.codec_context
        try:
            ctx.thread_type = "AUTO"
            ctx.thread_count = 4
        except Exception:  # already open (after a seek)
            pass
        return ctx

    def _frames(self, container, stream, ctx):
        for pkt in container.demux(stream):
            for frame in ctx.decode(pkt):
                yield frame

    def _show(self, frame) -> None:
        img = frame.reformat(width=self._out[0], height=self._out[1], format="rgb24",
                             interpolation="FAST_BILINEAR").to_image()
        self.frame = img

    def _play(self) -> None:
        import queue
        import time

        container, stream = self._open()
        tb = stream.time_base
        try:
            ctx = self._decoder(stream)
            frames = self._frames(container, stream, ctx)
            self.playing, base = True, time.monotonic()
            shown_at = 0.0
            self._seen = time.monotonic()
            while True:
                # commands: block while paused, peek while playing
                try:
                    cmd, arg = self._cmds.get(timeout=0.2) if not self.playing else self._cmds.get_nowait()
                except queue.Empty:
                    cmd, arg = None, 0.0
                if cmd == "close":
                    return
                if cmd == "toggle":
                    if self.ended:  # play again from the start
                        cmd, arg, self.position, self.ended = "seek", -1e9, 0.0, False
                        self.playing = True
                    else:
                        self.playing = not self.playing
                        base = time.monotonic() - self.position
                if cmd == "seek":
                    target = max(0.0, self.position + arg)
                    if self.duration:
                        target = min(target, max(0.0, self.duration - 0.5))
                    container.seek(int(target / tb), stream=stream, backward=True)
                    try:
                        ctx.flush_buffers()  # drop frames from before the seek
                    except Exception:
                        ctx = self._decoder(stream)
                    frames = self._frames(container, stream, ctx)
                    for frame in frames:  # decode up to the target, show that frame
                        if frame.pts is None or frame.pts * tb >= target - 0.02:
                            self._show(frame)
                            break
                    self.position, self.ended = target, False
                    base = time.monotonic() - target
                if not self.playing:
                    continue
                if time.monotonic() - self._seen > self.IDLE_PAUSE_S:
                    self.playing = False  # the screen moved on
                    continue
                frame = next(frames, None)
                if frame is None:
                    self.playing, self.ended = False, True
                    if self.duration:
                        self.position = self.duration
                    continue
                t = float(frame.pts * tb) if frame.pts is not None else self.position
                self.position = t
                now = time.monotonic()
                lag = now - (base + t)
                if lag > 0.1 and now - shown_at < 0.25:
                    continue  # late: skip scaling this one to catch up
                if lag > 0.3:  # can't keep up: run a little slow rather than freeze
                    base += lag
                elif lag < 0:
                    time.sleep(-lag)
                self._show(frame)
                shown_at = time.monotonic()
        finally:
            container.close()


def info(path: Path) -> dict:
    """{'duration', 'width', 'height', 'fps'} of a video, where known."""
    out: dict = {}
    try:
        import av

        with av.open(str(path)) as c:
            s = c.streams.video[0]
            out["width"], out["height"] = s.codec_context.width, s.codec_context.height
            if s.average_rate:
                out["fps"] = float(s.average_rate)
            if c.duration:
                out["duration"] = c.duration / 1e6
    except Exception:
        pass
    return out

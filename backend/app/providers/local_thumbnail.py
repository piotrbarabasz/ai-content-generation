"""Deterministic Pillow layouts and explicit, verified-source FFmpeg frame extraction."""

from io import BytesIO
from fractions import Fraction
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont, ImageOps, __version__ as pillow_version

from app.providers.ffmpeg_render import checksum
from app.runtime.media_process import MediaProcess


def decode_image(raw):
    if not raw or len(raw) > 32 * 1024 * 1024: raise ValueError("Thumbnail source must be a nonempty image under 32 MiB.")
    with Image.open(BytesIO(raw)) as image:
        if image.width * image.height > 16_000_000 or image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Thumbnail image format or dimensions are unsupported.")
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")


class LocalThumbnailBuilder:
    def __init__(self, ffmpeg=None, *, process=None):
        self.ffmpeg = Path(ffmpeg).resolve(strict=True) if ffmpeg else None
        self.process = process or MediaProcess(timeout=120)
        self.font_path = None
        for candidate in ("C:/Windows/Fonts/segoeuib.ttf", "DejaVuSans-Bold.ttf"):
            try:
                self.font_path = Path(ImageFont.truetype(candidate, 20).path).resolve(strict=True); break
            except OSError: continue

    def identity(self):
        return {"algorithm": "publishing-thumbnails-v1", "pillow": pillow_version,
                "font": checksum(self.font_path) if self.font_path else "pillow-default"}

    async def frames(self, root, frame_indices, *, fps=None, canceled=lambda: False):
        if not self.ffmpeg: raise ValueError("Configure trusted local FFmpeg to extract representative video frames.")
        outputs = []
        for index, frame in enumerate(frame_indices):
            name = f"frame-{index}.png"
            if type(frame) is not int or frame < 0: raise ValueError("Frame positions must be nonnegative integers.")
            command = [self.ffmpeg, "-nostdin", "-v", "error", "-xerror", "-n"]
            if fps is not None:
                rate = Fraction(str(fps))
                if rate <= 0: raise ValueError("Frame extraction requires a positive verified frame rate.")
                # Accurate input seeking on our verified CFR render avoids decoding
                # the full film separately for every suggested frame.
                command += ["-ss", f"{float(Fraction(frame, rate)):.9f}"]
            command += ["-i", "source.mp4", "-map", "0:v:0"]
            if fps is None: command += ["-vf", f"select=eq(n\\,{frame})"]
            command += ["-frames:v", "1", name]
            await self.process.run(command, cwd=root, canceled=canceled)
            raw = (root / name).read_bytes(); decode_image(raw); outputs.append(raw)
        return outputs

    def layouts(self, raw, *, headline, font_size=100, position="bottom", darkening=0.35, accent="#F3C553", logo=None):
        if (type(headline) is not str or not headline.strip() or len(headline) > 120 or "\n" in headline
                or type(font_size) is not int or not 24 <= font_size <= 200 or position not in {"top", "center", "bottom"}
                or type(darkening) not in (float, int) or not 0 <= darkening <= .85
                or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent)):
            raise ValueError("Invalid thumbnail headline, layout, contrast or accent.")
        source = decode_image(raw)
        scale = min(120, source.width // 16, source.height // 9)
        if scale < 10: raise ValueError("Thumbnail source is too small for readable 16:9 output.")
        size = (scale * 16, scale * 9)
        # Fit/letterbox without crop distortion or invented detail.
        base = ImageOps.pad(source, size, method=Image.Resampling.LANCZOS, color="#10141C")
        result = []
        for style in ("bold", "cinematic", "documentary"):
            overlay = darkening if style != "documentary" else min(.85, darkening + .15)
            canvas = Image.blend(base, Image.new("RGB", size, "black"), overlay)
            draw = ImageDraw.Draw(canvas)
            pixels = max(12, round(font_size * size[0] / 1920 * (.8 if style == "cinematic" else 1)))
            def face(n):
                return ImageFont.truetype(str(self.font_path), n) if self.font_path else ImageFont.load_default(size=n)
            words, lines, line = headline.split(), [], ""
            font = face(pixels)
            for word in words:
                proposal = (line + " " + word).strip()
                if draw.textbbox((0, 0), proposal, font=font)[2] > size[0] * .86 and line:
                    lines.append(line); line = word
                else: line = proposal
            lines.append(line)
            text = "\n".join(lines)
            while pixels > 12:
                font = face(pixels)
                box = draw.multiline_textbbox((0, 0), text, font=font, spacing=8)
                if box[2] <= size[0] * .86 and box[3] - box[1] <= size[1] * .65: break
                pixels -= 1
            box = draw.multiline_textbbox((0, 0), text, font=font, spacing=8)
            if box[2] > size[0] * .9 or box[3] - box[1] > size[1] * .7:
                raise ValueError("Headline is too long to remain readable; shorten it.")
            y = {"top": size[1] * .08, "center": (size[1] - box[3]) / 2, "bottom": size[1] * .88 - box[3]}[position]
            if style == "documentary":
                draw.rectangle((size[0] * .04, y - 10, size[0] * .96, y + box[3] + 10), fill="#10141C")
                draw.rectangle((size[0] * .04, y - 10, size[0] * .055, y + box[3] + 10), fill=accent)
            draw.multiline_text((size[0] * .07, y), text, font=font, fill=accent if style == "bold" else "white",
                                spacing=8, stroke_width=max(1, pixels // 30), stroke_fill="black")
            if logo:
                with Image.open(BytesIO(logo)) as mark:
                    mark = ImageOps.exif_transpose(mark).convert("RGBA")
                    mark.thumbnail((size[0] // 8, size[1] // 6))
                    canvas.paste(mark, (size[0] - mark.width - size[0] // 25, size[1] // 25), mark)
            stream = BytesIO(); canvas.save(stream, format="JPEG", quality=92, optimize=True)
            encoded = stream.getvalue(); verified = decode_image(encoded)
            if verified.size != size: raise ValueError("Thumbnail encoding changed dimensions.")
            result.append((style, encoded, size))
        return result

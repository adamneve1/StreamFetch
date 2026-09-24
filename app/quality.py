"""Safe, shared media-quality choices for web admission and the worker."""

QUALITY_HEIGHTS = {
    "best": None,
    "1080": 1080,
    "720": 720,
    "480": 480,
}


def validate(value):
    value = str(value or "best")
    if value not in QUALITY_HEIGHTS:
        raise ValueError("Pilih kualitas video yang tersedia.")
    return value


def ytdlp_selector(value):
    """Prefer editor-friendly AVC/AAC while respecting the height ceiling."""
    value = validate(value)
    height = QUALITY_HEIGHTS[value]
    cap = f"[height<={height}]" if height else ""
    return (
        f"bv*{cap}[vcodec^=avc1]+ba[acodec^=mp4a]/"
        f"bv*{cap}[vcodec^=avc1]+ba/"
        f"bv*{cap}+ba/b{cap}"
    )


def selected_media_info(metadata):
    """Return approximate bytes, hourly bytes and height from yt-dlp JSON."""
    formats = metadata.get("requested_formats") or [metadata]
    sizes = []
    bitrates = []
    heights = []
    for item in formats:
        size = item.get("filesize") or item.get("filesize_approx")
        if isinstance(size, (int, float)) and size > 0:
            sizes.append(int(size))
        bitrate = item.get("tbr")
        if isinstance(bitrate, (int, float)) and bitrate > 0:
            bitrates.append(float(bitrate))
        height = item.get("height")
        if isinstance(height, (int, float)) and height > 0:
            heights.append(int(height))
    duration = metadata.get("duration")
    estimated = sum(sizes) or None
    total_kbps = sum(bitrates)
    if not estimated and total_kbps and isinstance(duration, (int, float)) and duration > 0:
        estimated = int(total_kbps * 1000 / 8 * duration)
    per_hour = int(total_kbps * 1000 / 8 * 3600) if total_kbps else None
    return dict(estimated_bytes=estimated, bytes_per_hour=per_hour,
                height=max(heights, default=None),
                is_live=metadata.get("is_live") is True)

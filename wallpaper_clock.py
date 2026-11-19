"""Sample the system wallpaper using physical monitor coordinates."""
import os
import winreg
from pathlib import Path

def sample(rect, monitor, virtual):
    try:
        from PIL import Image, ImageStat
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as key:
            def value(name, default):
                try:
                    return winreg.QueryValueEx(key, name)[0]
                except OSError:
                    return default
            style = str(value("WallpaperStyle", "10"))
            tiled = str(value("TileWallpaper", "0")) == "1"
            paths = [Path(os.environ["APPDATA"]) / "Microsoft/Windows/Themes/TranscodedWallpaper",
                     Path(value("Wallpaper", ""))]
        if tiled:
            return None
        for path in paths:
            try:
                with Image.open(path) as img:
                    left, top, right, bottom = virtual if style == "22" else monitor
                    mw, mh = right-left, bottom-top
                    iw, ih = img.size
                    sx, sy = mw/iw, mh/ih
                    if style in ("10", "22"):
                        sx = sy = max(sx, sy)
                    elif style == "6":
                        sx = sy = min(sx, sy)
                    elif style != "2":
                        sx = sy = 1
                    ox, oy = left+(mw-iw*sx)/2, top+(mh-ih*sy)/2
                    x,y,w,h = rect
                    box = ((x-ox)/sx,(y-oy)/sy,(x+w-ox)/sx,(y+h-oy)/sy)
                    if box[0]<-0.5 or box[1]<-0.5 or box[2]>iw+0.5 or box[3]>ih+0.5:
                        return None
                    box=(max(0,box[0]),max(0,box[1]),min(iw,box[2]),min(ih,box[3]))
                    patch = img.convert("RGB").resize((32,32), Image.Resampling.BOX,box=box)
                    return "dark" if ImageStat.Stat(patch.convert("L")).mean[0]<145 else "light"
            except (ValueError,OSError):
                continue
    except (ImportError,OSError,KeyError,ValueError):
        pass
    return None

import math
import time
from tkinter import font as tkfont

class ClockFace:
    def __init__(self):
        self.font_key = None

    def draw(self, c, st, accent, palette, weather):
        c.delete('all')
        fg=accent if st['follow'] else st['color']
        clock=time.strftime('%H:%M:%S' if st['seconds'] else '%H:%M')
        date=time.strftime('%m月%d日')+'  星期'+ '一二三四五六日'[time.localtime().tm_wday]
        key = (st['font'], st['size'], st['bold'], st['date_size'])
        if key != self.font_key:
            self.time_font = tkfont.Font(family=key[0], size=key[1], weight='bold' if key[2] else 'normal')
            self.date_font = tkfont.Font(family=key[0], size=key[3])
            self.font_key = key
        f, df = self.time_font, self.date_font
        tw,th=f.measure(clock),f.metrics('linespace');dw,dh=(df.measure(date),df.metrics('linespace')) if st['date'] else (0,0)
        pad=st['padding']; horizontal=st['layout']=='左右排列'
        width=(tw+dw+pad if horizontal and dw else max(tw,dw))+pad*2
        height=(max(th,dh) if horizontal else th+dh)+pad*2
        extra=df.metrics('linespace') if weather else 0
        width=max(width,df.measure(weather)+pad*2)
        height+=extra
        content_height=height
        if st['shape']=='圆形':width=height=math.ceil(math.hypot(width,height))
        c.configure(width=width,height=height)
        if st['shape']!='透明':
            if st['shape']=='圆形':c.create_oval(1,1,width-1,height-1,fill=st['background'],outline='')
            elif st['shape']=='圆角卡片':
                r=min(24,height//3)
                c.create_polygon(r,0,width-r,0,width,0,width,r,width,height-r,width,height,width-r,height,r,height,0,height,0,height-r,0,r,0,0,fill=st['background'],smooth=True,splinesteps=24)
            else:c.create_rectangle(0,0,width,height,fill=st['background'],outline='')
        shadow=None
        is_transparent = st['shape'] == '透明'
        if palette and is_transparent:
            fg, shadow = palette
        elif is_transparent:
            # 自动计算前景色亮度，生成最细腻的互补微阴影，确保在任何复杂亮/暗壁纸上都清晰可读
            try:
                rgb = c.winfo_rgb(fg)
                lum = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000
                shadow = '#0b0f19' if lum > 32768 else '#ffffff'
            except Exception:
                shadow = '#0b0f19'

        def lettering(x, y, **options):
            if is_transparent and shadow:
                # 4方向微光晕 + 右下立体微投影，文字浮雕质感更强
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
                    c.create_text(x + dx, y + dy, **dict(options, fill=shadow))
            elif shadow:
                c.create_text(x + 1, y + 1, **dict(options, fill=shadow))
            c.create_text(x, y, **options)
        if horizontal and dw:
            total=tw+dw+pad;x=(width-total)/2
            lettering(x,(height-extra)/2,text=clock,font=f,fill=fg,anchor='w')
            lettering(x+tw+pad,(height-extra)/2,text=date,font=df,fill=fg,anchor='w')
        else:
            y=(height-extra-th-dh)/2
            lettering(width/2,y,text=clock,font=f,fill=fg,anchor='n')
            if dh:lettering(width/2,y+th,text=date,font=df,fill=fg,anchor='n')
        if weather:lettering(width/2,(height+content_height)/2-pad,text=weather,font=df,fill=fg,anchor='s')



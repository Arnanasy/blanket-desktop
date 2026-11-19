from display_geometry import bounds
from clock_face import ClockFace
from sys_monitor import SystemMonitor
"""Optional desktop clock and grouped edge launcher; standard library only."""
import ctypes
from ctypes import wintypes
from preferences import save_config
import os
from pathlib import Path
from threading import Thread
from concurrent.futures import Future, ThreadPoolExecutor
from local_weather import refresh as refresh_weather
from wallpaper_clock import sample
import time
import tkinter as tk
from tkinter import filedialog, messagebox, colorchooser, ttk, font as tkfont

u = ctypes.windll.user32
u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.GetForegroundWindow.restype = wintypes.HWND
u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u.GetAncestor.restype = wintypes.HWND
u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
u.SetWindowLongPtrW.restype = ctypes.c_ssize_t

def clock_should_show(enabled, foreground, clock_hwnd, class_name, was_visible, icons_visible=None, fullscreen=False):
    # 仅在启用时钟且桌面图标处于隐藏状态时显示
    if not enabled or icons_visible is not False:
        return False
    # 前台为全屏独占应用时隐藏，避免穿透或遮挡
    if fullscreen:
        return False
    return True

u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

class Widgets:
    def __init__(self, app):
        self.app = app; self.root = app.root
        self.cfg = app.cfg['widgets']
        self.face = ClockFace()
        self.groups = self.cfg.get('groups',{})
        if not isinstance(self.groups,dict): self.groups = {}
        self.groups = {k:[p for p in v if isinstance(p,str)] for k,v in self.groups.items() if isinstance(k,str) and isinstance(v,list)}
        for name in ('常用工具','临时文件'): self.groups.setdefault(name,[])
        self.accent = '#89B4FA'
        self.clock_on = tk.BooleanVar(value=self.cfg.get('clock',True))
        self.bar_on = tk.BooleanVar(value=self.cfg.get('bar',True))
        self.clock = tk.Toplevel(self.root); self.clock.withdraw(); self.clock.overrideredirect(True)
        self.clock.configure(bg='#010203'); self.clock.wm_attributes('-transparentcolor','#010203')
        defaults = dict(size=52,date_size=18,font='Segoe UI',color='#FFFFFF',background='#18202D',
                        shape='透明',layout='上下排列',bold=False,date=True,seconds=False,follow=False,opacity=100,padding=20)
        saved = self.cfg.get('appearance',{})
        self.style = defaults.copy()
        if isinstance(saved,dict):
            for k,v in saved.items():
                if k in defaults and type(v) is type(defaults[k]): self.style[k]=v
        for k,lo,hi in [('size',16,160),('date_size',10,60),('opacity',30,100),('padding',5,60)]:
            self.style[k]=max(lo,min(hi,self.style[k]))
        for k in ('color','background'):
            try:self.root.winfo_rgb(self.style[k])
            except tk.TclError:self.style[k]=defaults[k]
        self.adaptive = tk.BooleanVar(value=self.cfg.get('adaptive',False))
        self.palette = None
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.sampling = None
        self.sample_again = False
        self.save_timer = None
        self.hint_timer = None
        self.control_vars = {}
        self.recent = self.cfg.get('recent', [])
        self.recent = [v for v in self.recent if isinstance(v,str)][:8] if isinstance(self.recent,list) else []
        self.palettes = {'dark':['#F8FAFC','#64748B'], 'light':['#18202D','#525866']}
        supplied = self.cfg.get('wallpaper_palettes',{})
        import re
        for mode in self.palettes:
            pair = supplied.get(mode) if isinstance(supplied,dict) else None
            if isinstance(pair,list) and len(pair)==2 and all(isinstance(c,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in pair):
                self.palettes[mode]=pair
        self.weather_on=tk.BooleanVar(value=self.cfg.get('weather_enabled',True))
        cached=self.cfg.get('weather_cache')
        self.weather=cached if isinstance(cached,dict) and isinstance(cached.get('line'),str) and isinstance(cached.get('updated'),(int,float)) else {}
        self.weather_status='等待更新'
        self.weather_job=None
        self.weather_due=0
        self.weather_tip=None
        self.clock_label = tk.Canvas(self.clock,bg='#010203',highlightthickness=0)
        self.clock_label.pack()
        self.clock_label.bind('<Enter>',self.show_weather_tip)
        self.clock_label.bind('<Leave>',self.hide_weather_tip)
        self.clock_label.bind('<ButtonPress-1>',self.drag_start)
        self.clock_label.bind('<B1-Motion>',self.drag)
        self.clock_label.bind('<ButtonRelease-1>',lambda e:self.finish_drag())
        self.clock_label.bind('<Double-Button-1>',lambda e:self.app.toggle_theme())
        self.clock_label.bind('<Button-3>',lambda e:self.settings())
        self.clock_label.bind('<MouseWheel>',self.wheel)
        self.clock_label.bind('<Button-2>',lambda e:self.adjust('seconds',not self.style['seconds']))
        self.clock.update_idletasks()
        self.clock_hwnd = u.GetAncestor(self.clock.winfo_id(), 2)
        # Tool window, without activation: showing the clock must not take focus
        # away from Explorer and cause the next visibility check to hide it.
        exstyle = u.GetWindowLongPtrW(self.clock_hwnd, -20)
        u.SetWindowLongPtrW(self.clock_hwnd, -20, exstyle | 0x08000000 | 0x80)
        self.settings_window=None
        self.preview=None
        self.draw_clock()
        # ☀/☾ 一键切换深浅主题的桌面小按钮（拖动可移位，右键打开设置）
        tb=self.cfg.get('theme_btn',{})
        self.theme_btn_always=tk.BooleanVar(value=tb.get('always',True) if isinstance(tb,dict) else True)
        pos=tb.get('position') if isinstance(tb,dict) else None
        if not (isinstance(pos,list) and len(pos)==2 and all(type(v) is int for v in pos)):
            pos=[max(0,self.root.winfo_screenwidth()-72),12]
        self.theme_pos=pos
        self._theme_press=self._theme_drag=None
        self._theme_dark=None
        self.theme_btn=tk.Toplevel(self.root); self.theme_btn.withdraw(); self.theme_btn.overrideredirect(True)
        self.theme_btn.configure(bg='#010203'); self.theme_btn.wm_attributes('-transparentcolor','#010203')
        self.theme_canvas=tk.Canvas(self.theme_btn,bg='#010203',highlightthickness=0,cursor='hand2')
        self.theme_canvas.pack()
        self.theme_canvas.bind('<ButtonPress-1>',self.theme_drag_start)
        self.theme_canvas.bind('<B1-Motion>',self.theme_drag)
        self.theme_canvas.bind('<ButtonRelease-1>',self.theme_drag_end)
        self.theme_canvas.bind('<Button-3>',lambda e:self.settings())
        self.theme_btn.update_idletasks()
        self.theme_hwnd=u.GetAncestor(self.theme_btn.winfo_id(),2)
        exstyle=u.GetWindowLongPtrW(self.theme_hwnd,-20)
        u.SetWindowLongPtrW(self.theme_hwnd,-20,exstyle|0x08000000|0x80)
        self.draw_theme_button()
        self.bar = tk.Toplevel(self.root); self.bar.withdraw(); self.bar.overrideredirect(True)
        self.bar.configure(bg='#18202D'); self.bar.attributes('-topmost',True)
        self.expanded = set(); self.busy=False; self.last_inside=time.monotonic(); self.last_theme=0; self.last_clock=''
        self.edge_since = None
        self.edge_monitor = None
        self.last_desktop_foreground=None
        self.last_position_check = 0
        self.render_bar()
        self.monitor = SystemMonitor(self, bounds)
        self.root.after(150,self.tick)

    def save(self):
        self.cfg['weather_enabled']=self.weather_on.get()
        if self.weather:
            self.cfg['weather_cache']={k:self.weather[k] for k in ('line','updated','place','source','station') if k in self.weather}
        self.cfg.update(groups=self.groups,clock=self.clock_on.get(),bar=self.bar_on.get(),appearance=self.style,adaptive=self.adaptive.get(),recent=self.recent,wallpaper_palettes=self.palettes,theme_btn={'always':self.theme_btn_always.get(),'position':self.theme_pos})
        save_config(self.app.cfg)

    def wheel(self,event):
        if not event.delta:return
        direction=1 if event.delta>0 else -1
        if event.state & 4:
            self.adjust('size',max(16,min(160,self.style['size']+direction*2)))
        else:
            self.adjust('opacity',max(30,min(100,self.style['opacity']+direction*5)))
        return 'break'

    def adjust(self,key,value):
        self.style[key]=value
        if self.settings_window and self.settings_window.winfo_exists() and key in self.control_vars:
            self.control_vars[key].set(value)
        self.draw_clock()
        if self.preview and self.preview.winfo_exists():self.draw_clock(self.preview)
        label={'opacity':f'透明度 {value}%','size':f'字号 {value}','seconds':'显示秒' if value else '隐藏秒'}[key]
        self.clock_label.create_text(4,2,anchor='nw',text=label,fill='orange',tags='hint')
        if self.hint_timer:self.root.after_cancel(self.hint_timer)
        self.hint_timer=self.root.after(750,lambda:self.clock_label.delete('hint'))
        if self.save_timer:self.root.after_cancel(self.save_timer)
        self.save_timer=self.root.after(400,self.save)

    def sample_wallpaper(self):
        if not self.adaptive.get():
            self.palette=None
            self.draw_clock()
            return
        if self.sampling:
            self.sample_again=True
            return
        pos=self.cfg.get('position',[40,70])
        if not isinstance(pos,list) or len(pos)!=2 or not all(isinstance(v,int) for v in pos):pos=[40,70]
        x,y=pos
        w,h=self.clock.winfo_reqwidth(),self.clock.winfo_reqheight()
        l,t,vw,vh=[u.GetSystemMetrics(i) for i in (76,77,78,79)]
        self.sampling=self.worker.submit(sample,(x,y,w,h),bounds(x+w//2,y+h//2),(l,t,l+vw,t+vh))
        self.root.after(80,self.finish_sample)

    def finish_sample(self):
        if not self.sampling.done():
            self.root.after(80,self.finish_sample)
            return
        try:self.palette=self.sampling.result() if self.adaptive.get() else None
        except Exception:self.palette=None
        self.sampling=None
        self.draw_clock()
        if self.preview and self.preview.winfo_exists():self.draw_clock(self.preview)
        if self.sample_again:
            self.sample_again=False
            self.sample_wallpaper()

    def hide_weather_tip(self,event=None):
        if self.weather_tip:
            self.weather_tip.destroy()
            self.weather_tip=None

    def show_weather_tip(self,event):
        self.hide_weather_tip()
        if not self.weather_on.get():return
        details=self.weather_status
        if self.weather:
            stamp=time.strftime('%m-%d %H:%M',time.localtime(self.weather['updated']))
            details=f"{self.weather.get('place','')} · {stamp} 更新 · {details}"
        self.weather_tip=tk.Toplevel(self.clock)
        self.weather_tip.overrideredirect(True)
        self.weather_tip.geometry(f'+{event.x_root+15}+{event.y_root+20}')
        tk.Label(self.weather_tip,text=details+' · '+self.weather.get('source','Open-Meteo'),padx=5,pady=2).pack()
        self.weather_tip.after(2200,self.hide_weather_tip)

    def update_weather(self,now):
        if self.weather_job and self.weather_job.done():
            try:
                result=self.weather_job.result()
                if self.weather_on.get():
                    self.weather=result
                    self.weather_status='当地天气'
                    self.save()
                delay=300
            except Exception:
                self.weather_status='更新失败'
                delay=120
            self.weather_job=None
            self.weather_due=now+delay
            self.last_clock=''
        if self.weather_on.get() and self.weather_job is None and now>=self.weather_due:
            self.weather_status='正在更新'
            future = Future()
            self.weather_job = future
            previous = self.weather.copy()
            def fetch():
                try:
                    future.set_result(refresh_weather(previous))
                except Exception as error:
                    future.set_exception(error)
            Thread(target=fetch, name='weather', daemon=True).start()

    def toggle_weather(self):
        self.weather_due=0
        self.last_clock=''
        self.hide_weather_tip()
        self.save()

    def draw_clock(self, canvas=None):
        weather=''
        if self.weather_on.get():
            weather=self.weather.get('line') or ('天气暂不可用' if self.weather_status=='更新失败' else '正在获取当地天气…')
            city=self.weather.get('place')
            if city and city not in ('系统定位','网络定位'):
                weather=f'{city} · {weather}'
            if self.weather and (self.weather_status=='更新失败' or time.time()-self.weather['updated']>1800):
                weather+='（上次）'
        palette = self.palettes.get(self.palette) if self.adaptive.get() else None
        self.face.draw(canvas or self.clock_label, self.style, self.accent, palette, weather)
        if canvas is None:
            self.clock.attributes('-alpha', self.style['opacity']/100)

    def settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift();return
        w=tk.Toplevel(self.root);self.settings_window=w;w.title('时钟与桌面外观');w.geometry('610x700')
        w.minsize(520,550)
        toolbar = ttk.Frame(w)
        toolbar.pack(fill='x', padx=16, pady=(8,0))
        ttk.Button(toolbar,text='退出时隙',command=self.app.quit_app).pack(side='right')
        pane=ttk.Frame(w,padding=16);pane.pack(fill='both',expand=True)
        ttk.Label(pane,text='时钟外观 · 修改立即生效',font=('Microsoft YaHei UI',14)).pack(anchor='w')
        box=ttk.Frame(pane);box.pack(fill='x',pady=12)
        variables={}
        self.control_vars=variables
        def apply(*args):
            for key,var in variables.items():self.style[key]=var.get()
            self.draw_clock()
            if self.preview and self.preview.winfo_exists():self.draw_clock(self.preview)
            self.save()
        def row(label):
            f=ttk.Frame(box);f.pack(fill='x',pady=3);ttk.Label(f,text=label,width=12).pack(side='left');return f
        for key,label,lo,hi in [('size','时间字号',16,160),('date_size','日期字号',10,60),('padding','留白大小',5,60),('opacity','不透明度',30,100)]:
            var=tk.IntVar(value=self.style[key]);variables[key]=var
            tk.Scale(row(label),from_=lo,to=hi,orient='horizontal',variable=var,command=apply,showvalue=True,length=340).pack(side='left',fill='x',expand=True)
        all_families = sorted(set(tkfont.families()))
        preferred = ['Bahnschrift', 'Segoe UI Variable Display', 'Segoe UI', 'Microsoft YaHei UI', 'Consolas', 'Arial']
        font_choices = [f for f in preferred if f in all_families] + [f for f in all_families if f not in preferred]
        for key,label,values in [('font','字体',font_choices),('shape','形状',['透明','矩形卡片','圆角卡片','圆形']),('layout','排列',['上下排列','左右排列'])]:
            var=tk.StringVar(value=self.style[key]);variables[key]=var
            cb=ttk.Combobox(row(label),textvariable=var,values=values,state='readonly',width=30);cb.pack(side='left');cb.bind('<<ComboboxSelected>>',apply)
        def choose(key):
            color=colorchooser.askcolor(self.style[key],parent=w,title='选择颜色')[1]
            if color:
                self.style[key]=color
                if key=='color':variables['follow'].set(False)
                apply()
        colors=row('颜色')
        ttk.Button(colors,text='文字颜色',command=lambda:choose('color')).pack(side='left',padx=3)
        ttk.Button(colors,text='背景颜色',command=lambda:choose('background')).pack(side='left',padx=3)
        checks=ttk.Frame(box);checks.pack(fill='x',pady=7)
        for key,title in [('bold','加粗'),('date','显示日期'),('seconds','显示秒'),('follow','跟随系统颜色')]:
            var=tk.BooleanVar(value=self.style[key]);variables[key]=var
            ttk.Checkbutton(checks,text=title,variable=var,command=apply).pack(side='left')
        def toggle_adaptive():
            self.sample_wallpaper()
            self.save()
        ttk.Checkbutton(box,text='透明时钟按壁纸亮度配色',variable=self.adaptive,command=toggle_adaptive).pack(anchor='w')
        switches=ttk.Frame(pane);switches.pack(fill='x')
        ttk.Checkbutton(switches,text='当地天气',variable=self.weather_on,command=self.toggle_weather).pack(side='left')
        ttk.Checkbutton(switches,text='图标隐藏时显示时钟',variable=self.clock_on,command=self.save).pack(side='left')
        ttk.Checkbutton(switches,text='启用右侧启动栏',variable=self.bar_on,command=self.save).pack(side='left')
        ttk.Checkbutton(switches,text='主题按钮常显',variable=self.theme_btn_always,command=self.save).pack(side='left')
        ttk.Label(pane,text='图标隐藏时才显示时钟 · 可直接拖动 · 双击时钟或点 ☀/☾ 按钮一键切换深浅主题（右键设置）').pack(anchor='w',pady=(10,4))
        frame=ttk.Frame(pane);frame.pack(fill='both',expand=True)
        viewport=tk.Canvas(frame,bg='#334155',highlightthickness=0);viewport.grid(row=0,column=0,sticky='nsew')
        sx=ttk.Scrollbar(frame,orient='horizontal',command=viewport.xview);sx.grid(row=1,column=0,sticky='ew')
        sy=ttk.Scrollbar(frame,command=viewport.yview);sy.grid(row=0,column=1,sticky='ns')
        frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        viewport.configure(xscrollcommand=sx.set,yscrollcommand=sy.set)
        self.preview=tk.Canvas(viewport,bg='#334155',highlightthickness=0);viewport.create_window(0,0,window=self.preview,anchor='nw')
        self.preview.bind('<Configure>',lambda e:viewport.configure(scrollregion=viewport.bbox('all')))
        self.draw_clock(self.preview)
        ttk.Button(pane,text='完成（已自动保存）',command=w.destroy).pack(pady=(10,0))

    def place_clock(self,x,y,snap=False):
        l,t,r,b=bounds(x,y,work=True)
        width=self.clock.winfo_reqwidth();height=self.clock.winfo_reqheight()
        x=max(l,min(x,r-width));y=max(t,min(y,b-height))
        if snap:
            center_x = (l + r) // 2
            clock_center_x = x + width // 2
            if abs(x-l)<18:x=l
            elif abs(x+width-r)<18:x=max(l,r-width)
            elif abs(clock_center_x - center_x) < 22:x = center_x - width // 2
            if abs(y-t)<18:y=t
            elif abs(y+height-b)<18:y=max(t,b-height)
        u.SetWindowPos(self.clock_hwnd,None,x,y,0,0,0x0015)
        self.cfg['position']=[x,y]

    def drag_start(self,e): self.offset=(e.x_root-self.clock.winfo_x(),e.y_root-self.clock.winfo_y())
    def drag(self,e):
        self.place_clock(e.x_root-self.offset[0],e.y_root-self.offset[1])
    def finish_drag(self):
        self.place_clock(self.clock.winfo_x(),self.clock.winfo_y(),True)
        u.SetWindowPos(self.clock_hwnd,1,0,0,0,0,0x0013)
        self.save();self.sample_wallpaper()

    def fullscreen_active(self, hwnd, name):
        if not hwnd or name in ('Progman','WorkerW','Shell_TrayWnd','Shell_SecondaryTrayWnd'):
            return False
        rect=wintypes.RECT()
        if not u.GetWindowRect(hwnd,ctypes.byref(rect)):return False
        l,t,r,b=bounds((rect.left+rect.right)//2,(rect.top+rect.bottom)//2)
        return rect.left<=l and rect.top<=t and rect.right>=r and rect.bottom>=b

    def button(self,text,command):
        tk.Button(self.bar,text=text,command=command,bg='#18202D',fg=self.accent,activebackground='#28364A',activeforeground='white',relief='flat',anchor='w',padx=16,pady=7).pack(fill='x')
    def render_bar(self):
        for w in self.bar.winfo_children(): w.destroy()
        self.button('净桌面 · 快捷分组',self.app._tray_open)
        if self.recent:
            tk.Label(self.bar,text='最近启动',bg='#18202D',fg='white').pack(anchor='w',padx=14)
            for path in self.recent:
                self.button(Path(path).name,lambda p=path:self.open(p))
        for name,paths in self.groups.items():
            self.button(('▾ ' if name in self.expanded else '▸ ')+name,lambda n=name:self.fold(n))
            if name in self.expanded:
                for path in paths:
                    b=tk.Button(self.bar,text='  '+Path(path).stem,command=lambda p=path:self.open(p),bg='#233044',fg='white',relief='flat',anchor='w',padx=15)
                    b.pack(fill='x'); b.bind('<Button-3>',lambda e,n=name,p=path:self.remove(n,p))
                self.button('  ＋ 添加软件 / 文件',lambda n=name:self.add(n,False))
                self.button('  ＋ 添加文件夹',lambda n=name:self.add(n,True))
        self.button('暂停 / 恢复自动隐藏',self.app.pause_auto)
        self.button(('☀' if self._system_dark() else '☾')+' 一键切换深浅主题',self.app.toggle_theme)
        self.button('桌面组件设置',self.settings)
        self.bar.update_idletasks()
    def fold(self,name):
        if name in self.expanded:self.expanded.remove(name)
        else:self.expanded.add(name)
        self.render_bar()
    def add(self,name,folder):
        self.busy=True
        try:
            path=filedialog.askdirectory(parent=self.bar) if folder else filedialog.askopenfilename(parent=self.bar)
            if path and path not in self.groups[name]: self.groups[name].append(path); self.save(); self.render_bar()
        finally:self.busy=False; self.last_inside=time.monotonic()
    def remove(self,name,path):
        self.groups[name].remove(path); self.save(); self.render_bar()
    def open(self,path):
        try:
            os.startfile(path)
            self.recent=([path]+[p for p in self.recent if os.path.normcase(p)!=os.path.normcase(path)])[:8]
            self.save()
            self.bar.withdraw()
            self.render_bar()
        except OSError as e:messagebox.showerror('无法打开',str(e),parent=self.bar)
    def theme(self):
        self.sample_wallpaper()
        # Follow Windows wallpaper-derived accent when automatic accent is enabled.
        color=wintypes.DWORD(); opaque=wintypes.BOOL()
        try:
            if ctypes.windll.dwmapi.DwmGetColorizationColor(ctypes.byref(color),ctypes.byref(opaque))==0:
                rgb=[(color.value >> shift)&255 for shift in (16,8,0)]
                self.accent='#'+''.join(f'{max(120,c):02x}' for c in rgb)
                self.draw_clock(); self.render_bar()
        except OSError:pass
        self.refresh_theme_button()
    def _system_dark(self):
        try:
            from theme_switch import is_dark
            return bool(is_dark())
        except Exception:
            return False
    def draw_theme_button(self):
        dark=self._system_dark()
        self._theme_dark=dark
        c=self.theme_canvas
        c.configure(width=48,height=48)
        c.delete('all')
        c.create_oval(4,4,44,44,fill='#1E293B' if dark else '#FFF7E0',outline='#334155' if dark else '#F59E0B',width=2)
        c.create_text(24,24,text='☾' if dark else '☀',font=('Segoe UI Symbol',20),fill='#E2E8F0' if dark else '#D97706')
    def refresh_theme_button(self):
        if self._system_dark()!=self._theme_dark:self.draw_theme_button()
    def place_theme_button(self):
        l,t,r,b=bounds(*self.theme_pos,work=True)
        w=self.theme_btn.winfo_reqwidth();h=self.theme_btn.winfo_reqheight()
        x=max(l,min(self.theme_pos[0],r-w));y=max(t,min(self.theme_pos[1],b-h))
        self.theme_btn.geometry(f'+{x}+{y}')
        return x,y
    def theme_drag_start(self,e):
        self._theme_press=(e.x_root,e.y_root)
        self._theme_drag=(e.x_root-self.theme_btn.winfo_x(),e.y_root-self.theme_btn.winfo_y())
    def theme_drag(self,e):
        if not self._theme_drag:return
        l,t,r,b=bounds(e.x_root,e.y_root,work=True)
        w=self.theme_btn.winfo_reqwidth();h=self.theme_btn.winfo_reqheight()
        x=max(l,min(e.x_root-self._theme_drag[0],r-w));y=max(t,min(e.y_root-self._theme_drag[1],b-h))
        self.theme_btn.geometry(f'+{x}+{y}')
        self.theme_pos=[x,y]
    def theme_drag_end(self,e):
        if self._theme_press and abs(e.x_root-self._theme_press[0])+abs(e.y_root-self._theme_press[1])<=6:
            self.app.toggle_theme()
            self.refresh_theme_button()
        self._theme_press=self._theme_drag=None
        self.save()
    def tick(self):
        now=time.monotonic(); p=wintypes.POINT(); u.GetCursorPos(ctypes.byref(p))
        self.update_weather(now)
        if now-self.last_theme>30:self.theme();self.last_theme=now
        text=time.strftime('%H:%M:%S' if self.style['seconds'] else '%H:%M')
        if text!=self.last_clock:
            self.draw_clock();self.last_clock=text
            if self.preview and self.preview.winfo_exists():self.draw_clock(self.preview)
        visible=bool(self.clock.winfo_viewable())
        foreground=u.GetForegroundWindow()
        buf=ctypes.create_unicode_buffer(128);u.GetClassNameW(foreground,buf,128)
        desktop_foreground=foreground if buf.value in ('WorkerW','Progman') else None
        if desktop_foreground and desktop_foreground!=self.last_desktop_foreground:
            self.app.invalidate_desktop()
        self.last_desktop_foreground=desktop_foreground
        blocked=self.fullscreen_active(foreground, buf.value)
        if clock_should_show(self.clock_on.get(),foreground,self.clock_hwnd,buf.value,visible,
                             self.app.desktop_icons_visible(),blocked):
            if not visible:
                pos=self.cfg.get('position',[40,70])
                if not isinstance(pos,list) or len(pos)!=2 or not all(isinstance(v,int) for v in pos):pos=[40,70]
                l,t,r,b=bounds(*pos,work=True);x=max(l,min(pos[0],r-self.clock.winfo_reqwidth()));y=max(t,min(pos[1],b-self.clock.winfo_reqheight()))
                self.clock.geometry(f'{x:+d}{y:+d}')
                # SW_SHOWNOACTIVATE plus SWP_NOACTIVATE restores above the wallpaper
                # without making this widget the foreground application.
                # Use HWND_BOTTOM (1) so the desktop clock stays below all application windows.
                u.ShowWindow(self.clock_hwnd,4)
                u.SetWindowPos(self.clock_hwnd,1,x,y,0,0,0x0011)
        elif visible:self.clock.withdraw()
        if (self.theme_btn_always.get() or self.app.desktop_icons_visible() is False) and not blocked:
            if not self.theme_btn.winfo_viewable():
                x,y=self.place_theme_button()
                u.ShowWindow(self.theme_hwnd,4)
                u.SetWindowPos(self.theme_hwnd,1,x,y,0,0,0x0011)
        elif self.theme_btn.winfo_viewable():
            self.theme_btn.withdraw()
        if visible and now-self.last_position_check>1:
            self.place_clock(self.clock.winfo_x(),self.clock.winfo_y())
            self.last_position_check=now
        l,t,r,b=bounds(p.x,p.y)
        monitor_edge = self.monitor.tick(now, p.x, p.y, (l,t,r,b), blocked, self.bar_on.get())
        edge=self.bar_on.get() and p.x>=r-3 and not blocked and not monitor_edge
        monitor=(l,t,r,b)
        if not edge:
            self.edge_since=None
        elif self.edge_since is None or self.edge_monitor!=monitor:
            self.edge_since=now;self.edge_monitor=monitor
        if edge and self.edge_since is not None and now-self.edge_since>=0.35:
            wl,wt,wr,wb=bounds(p.x,p.y,work=True)
            self.bar.geometry(f'260x{min(self.bar.winfo_reqheight(),wb-wt)}+{wr-260}+{wt+max(0,(wb-wt-min(self.bar.winfo_reqheight(),wb-wt))//2)}')
            self.bar.deiconify();self.last_inside=now
        if self.bar.winfo_viewable():
            inside=self.bar.winfo_x()<=p.x<self.bar.winfo_x()+self.bar.winfo_width() and self.bar.winfo_y()<=p.y<self.bar.winfo_y()+self.bar.winfo_height()
            if inside:self.last_inside=now
            if not self.busy and (not self.bar_on.get() or blocked or now-self.last_inside>0.9):self.bar.withdraw()
        self.root.after(150,self.tick)

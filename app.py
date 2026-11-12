# -*- coding: utf-8 -*-
"""Two-image, three-click desktop assistant. Python 3.8 / Windows."""
import ctypes
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Ensure screenshot and mouse coordinates use the same pixel scale on Windows 7.
try:
    ctypes.windll.user32.SetProcessDPIAware()
except (AttributeError, OSError):
    pass

import cv2
import numpy as np
import pyautogui

pyautogui.FAILSAFE = True
CONFIG = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'ImageClickerWin7', 'config.json')
DEFAULTS = {'image1': '', 'image2': '', 'delay1': '1', 'delay2': '1', 'delay3': '1',
            'startup': '10', 'timeout': '30', 'threshold': '0.90', 'repeat': False}


def read_image(path):
    # imdecode handles Chinese paths on Windows, unlike some cv2.imread builds.
    try:
        data = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        image = None
    if image is None:
        raise ValueError('无法读取图片：' + path)
    if float(image.std()) < 1.0:
        raise ValueError('图片几乎是纯色，无法可靠识别：' + path)
    return image


def find_center(screen, template, threshold):
    h, w = template.shape[:2]
    if h > screen.shape[0] or w > screen.shape[1]:
        raise ValueError('参考图片比屏幕截图大，请重新截取目标区域。')
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, pos = cv2.minMaxLoc(result)
    if math.isfinite(score) and score >= threshold:
        return (pos[0] + w // 2, pos[1] + h // 2, score)
    return None


class App:
    def __init__(self, root):
        self.root = root
        root.title('图片识别自动点击')
        root.resizable(True, True)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        try:
            with open(CONFIG, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            if not isinstance(saved, dict):
                saved = {}
        except (OSError, ValueError):
            saved = {}
        self.vars = {key: (tk.BooleanVar(value=saved.get(key, val)) if key == 'repeat'
                           else tk.StringVar(value=str(saved.get(key, val))))
                     for key, val in DEFAULTS.items()}
        outer = ttk.Frame(root, padding=12)
        outer.grid(sticky='nsew')
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        for row, key, label in [(0, 'image1', '图片 1'), (1, 'image2', '图片 2（点击两次）')]:
            ttk.Label(outer, text=label).grid(row=row, column=0, sticky='w', pady=4)
            ttk.Entry(outer, textvariable=self.vars[key], width=52).grid(row=row, column=1, sticky='ew', padx=6)
            ttk.Button(outer, text='选择图片', command=lambda k=key: self.choose(k)).grid(row=row, column=2)
        fields = [('delay1', '点击图片 1 后等待（秒）'), ('delay2', '第一次点击图片 2 后等待（秒）'),
                  ('delay3', '第二次点击图片 2 后等待（秒，仅循环时）'), ('startup', '启动后等待（秒）'),
                  ('timeout', '每步最长识别时间（秒，0 为一直等待）'), ('threshold', '相似度（0.1～1，建议 0.90）')]
        for row, (key, label) in enumerate(fields, 2):
            ttk.Label(outer, text=label).grid(row=row, column=0, sticky='w', pady=4)
            ttk.Entry(outer, textvariable=self.vars[key], width=12).grid(row=row, column=1, sticky='w', padx=6)
        ttk.Checkbutton(outer, text='循环执行', variable=self.vars['repeat']).grid(row=8, column=0, sticky='w', pady=4)
        controls = ttk.Frame(outer)
        controls.grid(row=9, column=0, columnspan=3, sticky='w', pady=8)
        self.start_btn = ttk.Button(controls, text='保存并开始', command=self.start)
        self.start_btn.pack(side='left', padx=(0, 8))
        self.stop_btn = ttk.Button(controls, text='停止', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left')
        ttk.Label(outer, text='执行顺序：图片 1 → 图片 2 → 图片 2。将鼠标移到屏幕左上角可紧急停止。').grid(row=10, column=0, columnspan=3, sticky='w')
        self.log = tk.Text(outer, width=72, height=10, state='disabled', wrap='word')
        self.log.grid(row=11, column=0, columnspan=3, sticky='nsew', pady=(8, 0))
        outer.rowconfigure(11, weight=1)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.drain)

    def choose(self, key):
        path = filedialog.askopenfilename(filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp'), ('所有文件', '*.*')])
        if path:
            self.vars[key].set(path)

    def settings(self):
        values = {key: var.get() for key, var in self.vars.items()}
        for key in ('image1', 'image2'):
            if not os.path.isfile(values[key]):
                raise ValueError('请选择有效的' + ('图片 1' if key == 'image1' else '图片 2'))
        for key in ('delay1', 'delay2', 'delay3', 'startup', 'timeout', 'threshold'):
            try:
                number = float(values[key])
            except ValueError:
                raise ValueError('时间和相似度必须是数字。')
            if not math.isfinite(number) or number < 0:
                raise ValueError('时间和相似度必须是大于或等于 0 的有限数字。')
            values[key] = number
        if not 0.1 <= values['threshold'] <= 1:
            raise ValueError('相似度必须在 0.1 到 1 之间。')
        values['templates'] = [read_image(values['image1']), read_image(values['image2'])]
        return values

    def start(self):
        try:
            values = self.settings()
            os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
            with open(CONFIG, 'w', encoding='utf-8') as f:
                json.dump({key: var.get() for key, var in self.vars.items()}, f, ensure_ascii=False, indent=2)
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法开始', str(exc))
            return
        self.stop_event.clear()
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.worker = threading.Thread(target=self.run, args=(values,), daemon=True)
        self.worker.start()

    def emit(self, text):
        self.events.put(('log', text))

    def run(self, values):
        try:
            self.emit('启动：等待 {} 秒，请切换到目标窗口。'.format(values['startup']))
            if self.stop_event.wait(values['startup']):
                return
            cycle = 0
            while not self.stop_event.is_set():
                cycle += 1
                self.emit('第 {} 轮开始'.format(cycle))
                for step, index in enumerate((0, 1, 1), 1):
                    self.emit('步骤 {}：寻找图片 {}'.format(step, index + 1))
                    deadline = time.monotonic() + values['timeout'] if values['timeout'] else None
                    while not self.stop_event.is_set():
                        screenshot = pyautogui.screenshot()
                        frame = cv2.cvtColor(np.asarray(screenshot), cv2.COLOR_RGB2BGR)
                        match = find_center(frame, values['templates'][index], values['threshold'])
                        if match:
                            if self.stop_event.is_set():
                                return
                            pyautogui.click(match[0], match[1])
                            self.emit('已点击 ({}, {})，相似度 {:.3f}'.format(*match))
                            break
                        if deadline is not None and time.monotonic() >= deadline:
                            raise TimeoutError('步骤 {} 超时，未找到图片 {}。'.format(step, index + 1))
                        if self.stop_event.wait(0.35):
                            return
                    if self.stop_event.is_set():
                        return
                    if step < 3 or values['repeat']:
                        delay = values['delay{}'.format(step)]
                        self.emit('等待 {} 秒'.format(delay))
                        if self.stop_event.wait(delay):
                            return
                if not values['repeat']:
                    self.emit('执行完成。')
                    break
        except pyautogui.FailSafeException:
            self.emit('鼠标位于屏幕角落，已紧急停止。')
        except Exception as exc:
            self.emit('运行出错：{}: {}'.format(type(exc).__name__, exc))
        finally:
            self.events.put(('done', ''))

    def stop(self):
        self.stop_event.set()
        self.emit('正在停止……')
        self.stop_btn.config(state='disabled')

    def drain(self):
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == 'done':
                    self.start_btn.config(state='normal')
                    self.stop_btn.config(state='disabled')
                else:
                    self.log.config(state='normal')
                    self.log.insert('end', time.strftime('%H:%M:%S ') + text + '\n')
                    self.log.see('end')
                    self.log.config(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self.drain)

    def close(self):
        self.stop_event.set()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()

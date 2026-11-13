# -*- coding: utf-8 -*-
"""Configurable image-click and wait workflow. Python 3.8 / Windows."""
import ctypes
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# Prefer per-monitor DPI awareness on newer Windows; Windows 7 uses the fallback.
try:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()
except (AttributeError, OSError):
    pass

import cv2
import numpy as np
import pyautogui

pyautogui.FAILSAFE = True
CONFIG = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'ImageClickerWin7', 'config.json')
DEFAULTS = {'startup': '10', 'timeout': '30', 'threshold': '0.90', 'repeat': False}


def migrate_steps(saved):
    if isinstance(saved.get('steps'), list):
        return [dict(item) for item in saved['steps'] if isinstance(item, dict)]
    # Keep workflows configured by versions with two fixed pictures.
    if saved.get('image1') or saved.get('image2'):
        steps = []
        for image, delay in (('image1', 'delay1'), ('image2', 'delay2'), ('image2', 'delay3')):
            steps.append({'type': 'image', 'path': saved.get(image, '')})
            if delay != 'delay3' or saved.get('repeat'):
                steps.append({'type': 'wait', 'seconds': str(saved.get(delay, '1'))})
        return steps
    return []


def nonnegative(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(name + '必须是数字。')
    if not math.isfinite(number) or number < 0:
        raise ValueError(name + '必须是大于或等于 0 的有限数字。')
    return number


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


def mouse_coordinates(x, y, screenshot_size, desktop_size):
    width, height = screenshot_size
    desktop_width, desktop_height = desktop_size
    if min(width, height, desktop_width, desktop_height) <= 0:
        raise ValueError('无法获取有效的屏幕尺寸。')
    return (min(desktop_width - 1, int(x * desktop_width / width)),
            min(desktop_height - 1, int(y * desktop_height / height)))


class App:
    def __init__(self, root):
        self.root = root
        root.title('图片识别自动点击 - 流程编辑')
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
        self.steps = migrate_steps(saved)
        self.vars = {key: (tk.BooleanVar(value=saved.get(key, val)) if key == 'repeat'
                           else tk.StringVar(value=str(saved.get(key, val))))
                     for key, val in DEFAULTS.items()}
        outer = ttk.Frame(root, padding=12)
        outer.grid(sticky='nsew')
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        ttk.Label(outer, text='按顺序执行以下步骤。双击步骤可修改；图片会点击识别区域的中心。').grid(row=0, column=0, sticky='w', pady=(0, 6))
        self.tree = ttk.Treeview(outer, columns=('type', 'detail'), show='headings', height=8, selectmode='browse')
        self.tree.heading('type', text='类型')
        self.tree.heading('detail', text='内容')
        self.tree.column('type', width=100, stretch=False)
        self.tree.column('detail', width=510, stretch=True)
        self.tree.grid(row=1, column=0, sticky='nsew')
        self.tree.bind('<Double-1>', lambda event: self.edit_step())
        scrollbar = ttk.Scrollbar(outer, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        buttons = ttk.Frame(outer)
        buttons.grid(row=2, column=0, sticky='w', pady=7)
        for label, command in [('添加图片点击', self.add_image), ('添加等待', self.add_wait),
                               ('修改', self.edit_step), ('删除', self.delete_step),
                               ('上移', lambda: self.move_step(-1)), ('下移', lambda: self.move_step(1))]:
            ttk.Button(buttons, text=label, command=command).pack(side='left', padx=(0, 5))
        settings = ttk.LabelFrame(outer, text='运行设置', padding=8)
        settings.grid(row=3, column=0, sticky='ew')
        for col, (key, label) in enumerate([('startup', '启动等待（秒）'), ('timeout', '识别超时（秒，0 为不限）'), ('threshold', '相似度（0.1～1）')]):
            ttk.Label(settings, text=label).grid(row=0, column=col * 2, sticky='w', padx=(0, 4))
            ttk.Entry(settings, textvariable=self.vars[key], width=7).grid(row=0, column=col * 2 + 1, sticky='w', padx=(0, 10))
        ttk.Checkbutton(settings, text='循环执行', variable=self.vars['repeat']).grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))
        controls = ttk.Frame(outer)
        controls.grid(row=4, column=0, sticky='w', pady=8)
        self.save_btn = ttk.Button(controls, text='保存配置', command=self.save)
        self.save_btn.pack(side='left', padx=(0, 8))
        self.start_btn = ttk.Button(controls, text='保存并开始', command=self.start)
        self.start_btn.pack(side='left', padx=(0, 8))
        self.stop_btn = ttk.Button(controls, text='停止', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left')
        ttk.Label(outer, text='运行中可点击停止；将鼠标移至屏幕角落可紧急停止。').grid(row=5, column=0, sticky='w')
        self.log = tk.Text(outer, width=78, height=9, state='disabled', wrap='word')
        self.log.grid(row=6, column=0, sticky='nsew', pady=(8, 0))
        outer.rowconfigure(6, weight=1)
        self.refresh()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.drain)

    def refresh(self, selected=None):
        self.tree.delete(*self.tree.get_children())
        for index, step in enumerate(self.steps):
            if step.get('type') == 'image':
                kind, detail = '图片点击', step.get('path', '')
            else:
                kind, detail = '等待', str(step.get('seconds', '')) + ' 秒'
            self.tree.insert('', 'end', iid=str(index), values=('{}  {}'.format(index + 1, kind), detail))
        if selected is not None and 0 <= selected < len(self.steps):
            self.tree.selection_set(str(selected))
            self.tree.focus(str(selected))
            self.tree.see(str(selected))

    def selected_index(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo('请选择步骤', '请先在列表中选择一个步骤。')
            return None
        return int(selection[0])

    def insert_step(self, step):
        selection = self.tree.selection()
        index = int(selection[0]) + 1 if selection else len(self.steps)
        self.steps.insert(index, step)
        self.refresh(index)

    def add_image(self):
        path = filedialog.askopenfilename(title='选择要识别并点击的图片', filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp'), ('所有文件', '*.*')])
        if path:
            self.insert_step({'type': 'image', 'path': path})

    def ask_wait(self, initial='1'):
        result = simpledialog.askstring('等待步骤', '等待多少秒？可以输入小数：', initialvalue=str(initial), parent=self.root)
        if result is None:
            return None
        try:
            nonnegative(result, '等待时间')
        except ValueError as exc:
            messagebox.showerror('输入错误', str(exc))
            return None
        return result.strip()

    def add_wait(self):
        seconds = self.ask_wait()
        if seconds is not None:
            self.insert_step({'type': 'wait', 'seconds': seconds})

    def edit_step(self):
        index = self.selected_index()
        if index is None:
            return
        step = self.steps[index]
        if step.get('type') == 'image':
            path = filedialog.askopenfilename(title='更换图片', filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp'), ('所有文件', '*.*')])
            if path:
                step['path'] = path
        else:
            seconds = self.ask_wait(step.get('seconds', '1'))
            if seconds is not None:
                step['seconds'] = seconds
        self.refresh(index)

    def delete_step(self):
        index = self.selected_index()
        if index is not None:
            self.steps.pop(index)
            self.refresh(min(index, len(self.steps) - 1))

    def move_step(self, direction):
        index = self.selected_index()
        if index is not None and 0 <= index + direction < len(self.steps):
            other = index + direction
            self.steps[index], self.steps[other] = self.steps[other], self.steps[index]
            self.refresh(other)

    def settings(self):
        values = {key: var.get() for key, var in self.vars.items()}
        for key, label in [('startup', '启动等待'), ('timeout', '识别超时'), ('threshold', '相似度')]:
            values[key] = nonnegative(values[key], label)
        if not 0.1 <= values['threshold'] <= 1:
            raise ValueError('相似度必须在 0.1 到 1 之间。')
        if not self.steps:
            raise ValueError('请至少添加一个步骤。')
        values['steps'] = []
        templates = {}
        for index, step in enumerate(self.steps, 1):
            kind = step.get('type')
            if kind == 'wait':
                values['steps'].append({'type': 'wait', 'seconds': nonnegative(step.get('seconds'), '步骤 {} 的等待时间'.format(index))})
            elif kind == 'image':
                path = step.get('path', '')
                if not os.path.isfile(path):
                    raise ValueError('步骤 {} 的图片不存在，请重新选择：{}'.format(index, path))
                if path not in templates:
                    templates[path] = read_image(path)
                values['steps'].append({'type': 'image', 'path': path, 'template': templates[path]})
            else:
                raise ValueError('步骤 {} 类型无效，请删除并重新添加。'.format(index))
        if values['repeat'] and all(step['type'] == 'wait' and step['seconds'] == 0 for step in values['steps']):
            raise ValueError('循环流程不能全部是 0 秒等待，请添加有效步骤。')
        return values

    def save_config(self):
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        data = {key: var.get() for key, var in self.vars.items()}
        data['steps'] = self.steps
        with open(CONFIG, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save(self):
        try:
            self.settings()
            self.save_config()
            self.emit('配置已保存。')
        except (OSError, ValueError) as exc:
            messagebox.showerror('无法保存', str(exc))

    def start(self):
        try:
            values = self.settings()
            self.save_config()
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
                for number, step in enumerate(values['steps'], 1):
                    if self.stop_event.is_set():
                        return
                    if step['type'] == 'wait':
                        self.emit('步骤 {}：等待 {} 秒'.format(number, step['seconds']))
                        if self.stop_event.wait(step['seconds']):
                            return
                        continue
                    self.emit('步骤 {}：寻找 {}'.format(number, os.path.basename(step['path'])))
                    deadline = time.monotonic() + values['timeout'] if values['timeout'] else None
                    while not self.stop_event.is_set():
                        screenshot = pyautogui.screenshot()
                        frame = cv2.cvtColor(np.asarray(screenshot), cv2.COLOR_RGB2BGR)
                        match = find_center(frame, step['template'], values['threshold'])
                        if match:
                            if self.stop_event.is_set():
                                return
                            desktop = pyautogui.size()
                            shot_size = (frame.shape[1], frame.shape[0])
                            x, y = mouse_coordinates(match[0], match[1], shot_size, desktop)
                            self.emit('识别中心 ({}, {})；截图 {}×{}，鼠标坐标范围 {}×{}；点击 ({}, {})，相似度 {:.3f}'.format(
                                match[0], match[1], shot_size[0], shot_size[1], desktop[0], desktop[1], x, y, match[2]))
                            pyautogui.click(x, y)
                            break
                        if deadline is not None and time.monotonic() >= deadline:
                            raise TimeoutError('步骤 {} 超时，未找到图片 {}。'.format(number, os.path.basename(step['path'])))
                        if self.stop_event.wait(0.35):
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

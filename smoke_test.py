import time
import numpy as np
import cv2
import app

rng = np.random.RandomState(42)
template1 = rng.randint(0, 256, (16, 18, 3), dtype=np.uint8)
template2 = rng.randint(0, 256, (14, 12, 3), dtype=np.uint8)
frame = np.zeros((100, 120, 3), dtype=np.uint8)
frame[20:36, 30:48] = template1
frame[60:74, 70:82] = template2
frame[21, 31] = 0
assert app.find_center(frame, template1, 0.90)[:2] == (39, 28)
assert app.find_center(frame, template2, 0.90)[:2] == (76, 67)
assert app.find_center(np.zeros_like(frame), template1, 0.90) is None

class Screen:
    def __array__(self, dtype=None):
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
class Stop:
    def __init__(self): self.stopped = False
    def is_set(self): return self.stopped
    def wait(self, seconds): return self.stopped
class Events:
    def __init__(self): self.items = []
    def put(self, item): self.items.append(item)
class Fake:
    def __init__(self):
        self.stop_event = Stop()
        self.events = Events()
        self.logs = []
    def emit(self, message): self.logs.append(message)

assert app.mouse_coordinates(150, 100, (200, 200), (100, 100)) == (75, 50)
assert app.mouse_coordinates(150, 100, (200, 200), (200, 200)) == (150, 100)
clicks = []
app.pyautogui.size = lambda: (120, 100)
app.pyautogui.screenshot = lambda: Screen()
app.pyautogui.click = lambda x, y: clicks.append((x, y))
job = Fake()
app.App.run(job, {'startup': 0, 'timeout': 1, 'threshold': .9,
                  'templates': [template1, template2], 'repeat': False,
                  'delay1': 0, 'delay2': 0, 'delay3': 0})
assert clicks == [(39, 28), (76, 67), (76, 67)], (clicks, job.logs)
assert '执行完成。' in job.logs, job.logs
assert ('done', '') in job.events.items

job = Fake()
job.stop_event.stopped = True
clicks.clear()
app.App.run(job, {'startup': 0, 'timeout': 1, 'threshold': .9,
                  'templates': [template1, template2], 'repeat': False,
                  'delay1': 0, 'delay2': 0, 'delay3': 0})
assert clicks == []
print('PASS: approximate matching, click order, cancellation, completion')

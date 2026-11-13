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
assert app.mouse_coordinates(150, 100, (200, 200), (100, 100)) == (75, 50)
assert app.mouse_coordinates(150, 100, (200, 200), (200, 200)) == (150, 100)

old = {'image1': 'a.png', 'image2': 'b.png', 'delay1': '2', 'delay2': '3', 'delay3': '4', 'repeat': True}
assert app.migrate_steps(old) == [{'type':'image', 'path':'a.png'}, {'type':'wait', 'seconds':'2'},
                                {'type':'image', 'path':'b.png'}, {'type':'wait', 'seconds':'3'},
                                {'type':'image', 'path':'b.png'}, {'type':'wait', 'seconds':'4'}]
old['repeat'] = False
assert len(app.migrate_steps(old)) == 5
assert app.migrate_steps({'steps': []}) == []

class Screen:
    def __array__(self, dtype=None):
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
class Stop:
    def __init__(self): self.stopped = False; self.waits = []
    def is_set(self): return self.stopped
    def wait(self, seconds):
        self.waits.append(seconds)
        return self.stopped
class Events:
    def __init__(self): self.items = []
    def put(self, item): self.items.append(item)
class Fake:
    def __init__(self): self.stop_event = Stop(); self.events = Events(); self.logs = []
    def emit(self, message): self.logs.append(message)

clicks = []
app.pyautogui.size = lambda: (120, 100)
app.pyautogui.screenshot = lambda: Screen()
app.pyautogui.click = lambda x, y: clicks.append((x, y))
steps = [{'type': 'image', 'path': 'a.png', 'template': template1},
         {'type': 'wait', 'seconds': 2.5},
         {'type': 'image', 'path': 'b.png', 'template': template2},
         {'type': 'wait', 'seconds': 0.5},
         {'type': 'image', 'path': 'a.png', 'template': template1}]
values = {'startup': 10, 'timeout': 1, 'threshold': .9, 'steps': steps, 'repeat': False}
job = Fake()
app.App.run(job, values)
assert clicks == [(39, 28), (76, 67), (39, 28)], (clicks, job.logs)
assert job.stop_event.waits == [10, 2.5, .5], job.stop_event.waits
assert '执行完成。' in job.logs, job.logs
assert ('done', '') in job.events.items
job = Fake()
job.stop_event.stopped = True
clicks.clear()
app.App.run(job, values)
assert clicks == []
print('PASS: approximate matching, DPI coordinates, migration, workflow order, waits, cancellation')

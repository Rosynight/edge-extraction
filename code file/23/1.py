import cv2
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox
import os.path as osp
import time
import atexit
import os

# ---------------------------- 全局 tk 根窗口（单例）----------------------------

_root = tk.Tk()
_root.withdraw()
_root.update_idletasks()
sw, sh = _root.winfo_screenwidth(), _root.winfo_screenheight()
w, h = 300, 100
_root.geometry(f'{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}')
_root.attributes('-topmost', True)

# ---------------------------- 常量 ----------------------------

GC_ITER = 5
GC_RADIUS = 3
CAM_WIN = 'camera_snap'

# ---------------------------- 摄像头清理注册 ----------------------------

cap_clean = None

def _ensure_cam_release():
    global cap_clean
    if cap_clean:
        cap_clean.release()
        cv2.destroyAllWindows()

atexit.register(_ensure_cam_release)

# ---------------------------- 主类 ----------------------------

class GrabCutTool:
    def __init__(self):
        self.img = None
        self.mask = None
        self.display = None
        self.rect = None
        self.drawing = False
        self.rect_done = False
        self.mode = 'rect'
        self.out = None
        self.ix, self.iy = -1, -1

    # ---------------- 文件对话框（复用全局 _root） ----------------
    def _select_file(self, title, save=False, default_ext='.png', ftypes=None):
        if not save:
            path = filedialog.askopenfilename(
                parent=_root,
                title=title,
                filetypes=ftypes or [('图片', '*.jpg *.jpeg *.png *.bmp *.tiff')])
        else:
            path = filedialog.asksaveasfilename(
                parent=_root,
                title=title,
                defaultextension=default_ext,
                filetypes=ftypes or [('PNG', '*.png')])
        return path

    # ---------------- 中文路径安全读写 ----------------
    @staticmethod
    def imread(p):
        return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

    @staticmethod
    def imwrite(p, img):
        ext = osp.splitext(p)[-1].lower() or '.png'
        if not ext.startswith('.'):
            ext = '.' + ext
        flag, buf = cv2.imencode(ext, img)
        if flag:
            with open(p, 'wb') as f:
                f.write(buf.tobytes())

    # ---------------- 找第一个可用摄像头 ----------------
    @staticmethod
    def _find_first_camera():
        for idx in range(4):
            cap = cv2.VideoCapture(idx)
            if cap.read()[0]:
                cap.release()
                return idx
        return None

    # ---------------- 摄像头采集 ----------------
    def _capture_from_camera(self):
        idx = self._find_first_camera()
        if idx is None:
            messagebox.showerror('错误', '未检测到可用摄像头')
            return None
        cap = cv2.VideoCapture(idx)
        global cap_clean
        cap_clean = cap
        cv2.namedWindow(CAM_WIN)
        print('摄像头已打开，按 q 拍照，ESC 直接退出')
        while True:
            ret, frame = cap.read()
            if not ret or cv2.getWindowProperty(CAM_WIN, cv2.WND_PROP_VISIBLE) < 1:
                break
            cv2.imshow(CAM_WIN, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                snap = frame.copy()
                cv2.destroyWindow(CAM_WIN)
                cap_clean = None
                cap.release()
                return snap
            elif key == 27:
                cv2.destroyWindow(CAM_WIN)
                cap_clean = None
                cap.release()
                return None
        cv2.destroyWindow(CAM_WIN)
        cap_clean = None
        cap.release()
        return None

    # ---------------- 主流程 ----------------
    def run(self):
        choice = self._choose_source()
        if choice == 'file':
            path = self._select_file('选择一张图片')
            if not path or not osp.isfile(path):
                print('未选择有效文件，程序退出')
                return
            self.img = self.imread(path)
        elif choice == 'camera':
            self.img = self._capture_from_camera()
            if self.img is None:
                print('未成功拍照，程序退出')
                return
            snap_path = f'snap_{int(time.time())}.png'
            self.imwrite(snap_path, self.img)
            print('已保存快照：', osp.abspath(snap_path))
        else:
            print('取消选择，程序退出')
            return

        # 强制转 3 通道
        if len(self.img.shape) == 2:
            self.img = cv2.cvtColor(self.img, cv2.COLOR_GRAY2BGR)

        self.display = self.img.copy()
        self.mask = np.zeros(self.img.shape[:2], dtype=np.uint8)
        cv2.namedWindow('image')
        cv2.setMouseCallback('image', self.mouse_cb)
        self._put_tip('1. 拖矩形框住前景，按 ENTER 初步提取')
        cv2.imshow('image', self.display)

        while True:
            k = cv2.waitKey(1) & 0xFF
            if k == 13 and self.mode == 'rect' and self.rect is not None:
                self._first_grabcut()
            elif k == ord('n') and self.mode == 'mask':
                self._second_grabcut()
            elif k == 19:  # Ctrl+S
                self._save_foreground()
            elif k == ord('r'):
                self._reset_rect()
            elif k == ord('q'):
                self._reset_mask()
            elif k == 27:
                break
        cv2.destroyAllWindows()

    # ---------------- 选择输入源 ----------------
    def _choose_source(self):
        var = tk.StringVar(value='')
        btn_win = tk.Toplevel(_root)
        btn_win.title('选择输入源')
        btn_win.geometry(f'{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}')
        btn_win.resizable(False, False)
        btn_win.attributes('-topmost', True)

        def set_file():
            var.set('file')
            btn_win.destroy()

        def set_camera():
            var.set('camera')
            btn_win.destroy()

        tk.Button(btn_win, text='打开本地图片', command=set_file).pack(pady=10)
        tk.Button(btn_win, text='打开摄像头拍照', command=set_camera).pack(pady=10)
        btn_win.wait_window()
        return var.get()

    # ---------------- 鼠标回调 ----------------
    def mouse_cb(self, event, x, y, flags, param):
        if self.mode == 'rect' and not self.rect_done:
            if event == cv2.EVENT_LBUTTONDOWN:
                self.ix, self.iy = x, y
                self.drawing = True
            elif event == cv2.EVENT_MOUSEMOVE and self.drawing:
                tmp = self.display.copy()
                cv2.rectangle(tmp, (self.ix, self.iy), (x, y), (0, 255, 0), 2)
                cv2.imshow('image', tmp)
            elif event == cv2.EVENT_LBUTTONUP:
                self.drawing = False
                x1, y1 = min(self.ix, x), min(self.iy, y)
                x2, y2 = max(self.ix, x), max(self.iy, y)
                self.rect = (x1, y1, x2 - x1, y2 - y1)
                cv2.rectangle(self.display, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.imshow('image', self.display)
        elif self.mode == 'mask':
            if event == cv2.EVENT_LBUTTONDOWN:
                self.drawing, self.value = True, 1
            elif event == cv2.EVENT_RBUTTONDOWN:
                self.drawing, self.value = True, 0
            elif event == cv2.EVENT_MOUSEMOVE and self.drawing:
                h, w = self.mask.shape[:2]
                x = np.clip(x, 0, w - 1)
                y = np.clip(y, 0, h - 1)
                cv2.circle(self.display, (x, y), GC_RADIUS, (255, 255, 255) if self.value else (0, 0, 0), -1)
                cv2.circle(self.mask, (x, y), GC_RADIUS, self.value, -1)
                cv2.imshow('image', self.display)
            elif event in (cv2.EVENT_LBUTTONUP, cv2.EVENT_RBUTTONUP):
                self.drawing = False

    # ---------------- 业务函数 ----------------
    def _first_grabcut(self):
        if self.rect[2] <= 0 or self.rect[3] <= 0:
            messagebox.showwarning('提示', '矩形宽或高为 0，请重新画框！')
            return
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        cv2.grabCut(self.img, self.mask, self.rect, bgd, fgd, GC_ITER, cv2.GC_INIT_WITH_RECT)
        self._show_result()
        self.rect_done = True
        self.mode = 'mask'
        self._put_tip('2. 左键=前景白线，右键=背景黑线，按 N 再次提取')

    def _second_grabcut(self):
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        cv2.grabCut(self.img, self.mask, None, bgd, fgd, GC_ITER, cv2.GC_INIT_WITH_MASK)
        self._show_result()
        self._put_tip('可按 Ctrl+S 保存，或继续画线后按 N 迭代，ESC 退出')

    def _show_result(self):
        mask2 = np.where((self.mask == 2) | (self.mask == 0), 0, 1).astype('uint8')
        self.out = self.img * mask2[:, :, np.newaxis]
        b, g, r = cv2.split(self.out)
        alpha = mask2 * 255
        self.out = cv2.merge([b, g, r, alpha])
        cv2.imshow('result', self.out)

    def _save_foreground(self):
        if self.out is None:
            messagebox.showwarning('提示', '尚未提取出前景，无法保存')
            return
        path = self._select_file('保存前景为 PNG', save=True)
        if path:
            self.imwrite(path, self.out)
            print('已保存前景:', osp.abspath(path))

    def _reset_rect(self):
        self.rect = None
        self.rect_done = False
        self.mode = 'rect'
        self.mask = np.zeros(self.img.shape[:2], dtype=np.uint8)
        self.display = self.img.copy()
        self._put_tip('1. 拖矩形框住前景，按 ENTER 初步提取')
        cv2.imshow('image', self.display)

    def _reset_mask(self):
        if self.mode != 'mask':
            return
        self.mask = np.where(self.mask == 0, 0, 2)
        self.display = self.img.copy()
        self._put_tip('手绘 mask 已清空，可重画线条')

    def _put_tip(self, text):
        self.display = self.img.copy()
        cv2.putText(self.display, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

# ---------------------------- 入口 ----------------------------

if __name__ == '__main__':
    GrabCutTool().run()
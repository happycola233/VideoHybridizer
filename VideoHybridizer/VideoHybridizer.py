#  pip install opencv-python moviepy pywin32

import cv2  # 导入OpenCV库，用于视频处理
import os  # 导入os库，用于文件路径操作
import shutil  # 导入shutil库，用于文件操作
import threading  # 导入threading库，用于多线程处理
import time  # 导入time库，用于时间处理
import ctypes, win32api, win32con, win32gui, win32print  # 导入Windows高分辨率缩放适配所需的库
from tkinter import Tk, Text, Scrollbar, END, DISABLED, NORMAL, Frame  # 导入Tkinter库的必要组件
from tkinter import filedialog, messagebox, StringVar, IntVar  # 导入Tkinter的对话框和变量组件
from tkinter.ttk import Button, Label, Entry, Checkbutton, Progressbar, Style  # 导入Tkinter的ttk风格组件
import tempfile  # 导入tempfile模块以创建临时文件和目录
from moviepy.editor import VideoFileClip  # 导入moviepy的VideoFileClip类，用于视频编辑

def select_video(label_var):
    # 弹出文件选择对话框，让用户选择视频文件
    file_path = filedialog.askopenfilename(
        title="选择视频文件",
        filetypes=[("视频文件", "*.MP4 *.AVI *.MOV *.MKV *.WMV"), ("所有文件", "*.*")]
    ).replace("/","\\")
    if file_path:
        label_var.set(file_path)  # 设置选择的文件路径到相应的StringVar变量中
    return None

def select_output_file(label_var):
    # 弹出文件保存对话框，让用户选择输出文件位置和文件名
    file_path = filedialog.asksaveasfilename(
        title="选择导出位置和文件名",
        defaultextension=".mp4",
        filetypes=[("MP4文件", "*.mp4"), ("AVI文件", "*.avi"), ("MOV文件", "*.mov"), ("MKV文件", "*.mkv"), ("WMV文件", "*.wmv"), ("所有文件", "*.*")]
    ).replace("/","\\")
    if file_path:
        label_var.set(file_path)  # 设置选择的文件路径到相应的StringVar变量中
    return None

def convert_to_60fps(input_video_path, output_video_path, log_callback, progress_callback):  # 第4步
    # 使用moviepy将视频转换为60帧
    clip = VideoFileClip(input_video_path)  # 加载输入视频
    fps = clip.fps  # 获取视频帧率

    if fps == 60:
        log_callback(f"视频 {input_video_path} 已经是60帧，无需转换\n")  # 如果已经是60帧，记录日志并跳过转换
        shutil.copy(input_video_path, output_video_path)  # 直接复制视频文件
        return

    total_frames = int(clip.duration * 60)  # 获取转换后的视频总帧数
    last_frame_count = -1  # 初始化最后的帧计数
    def update_progress(gf, t):
        nonlocal last_frame_count  # 使用nonlocal声明引用外部变量
        frame_count = int(t * 60)  # 计算当前帧数
        if frame_count != last_frame_count:
            progress_callback(frame_count, total_frames)  # 更新进度条
            if frame_count % 60 == 0 or frame_count == total_frames:
                log_callback(f"转换进度: {frame_count}/{total_frames} 帧")  # 记录进度日志
            last_frame_count = frame_count
        return gf(t)

    new_clip = clip.fl(update_progress)  # 应用进度更新函数
    new_clip.write_videofile(output_video_path, fps=60, temp_audiofile=os.path.join(temp_dir, "temp_audiofile.mp3"), remove_temp=True)  # 写入新视频文件，并指定临时音频文件路径

    log_callback(f"视频 {input_video_path} 转换为60帧完成！\n")  # 记录转换完成日志

def merge_videos(video_a_path, video_b_path, output_path, progress_callback, log_callback):  # 第3步
    # 把两个视频转换为60帧
    temp_video_a = os.path.join(temp_dir, "temp_a.mp4").replace("/","\\")  # 创建第一个临时视频文件路径
    temp_video_b = os.path.join(temp_dir, "temp_b.mp4").replace("/","\\")  # 创建第二个临时视频文件路径
    
    log_callback(f"开始转换视频 {video_a_path} 为60帧......")
    convert_to_60fps(video_a_path, temp_video_a, log_callback, progress_callback)  # 转换第一个视频为60帧
    log_callback(f"开始转换视频 {video_b_path} 为60帧......")
    convert_to_60fps(video_b_path, temp_video_b, log_callback, progress_callback)  # 转换第二个视频为60帧

    # 合并两个60帧视频，生成120帧视频
    log_message("开始合成视频......")
    cap_a = cv2.VideoCapture(temp_video_a)  # 打开第一个临时视频文件
    cap_b = cv2.VideoCapture(temp_video_b)  # 打开第二个临时视频文件
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # 设置视频编码格式
    out = cv2.VideoWriter(output_path, fourcc, 120.0, (int(cap_a.get(3)), int(cap_a.get(4))))  # 创建120帧视频写入对象

    total_frames = int(cap_a.get(cv2.CAP_PROP_FRAME_COUNT))  # 获取视频总帧数
    frame_count = 0  # 初始化帧计数

    while True:
        ret_a, frame_a = cap_a.read()  # 读取第一个视频帧
        ret_b, frame_b = cap_b.read()  # 读取第二个视频帧
        if not ret_a or not ret_b:
            break
        out.write(frame_a)  # 写入第一个视频帧
        out.write(frame_b)  # 写入第二个视频帧
        frame_count += 1
        progress_callback(frame_count, total_frames)  # 更新进度条
        if frame_count % 60 == 0 or frame_count == total_frames:
            log_callback(f"处理进度: {frame_count}/{total_frames} 帧")  # 每60帧记录一次日志

    cap_a.release()  # 释放第一个视频文件
    cap_b.release()  # 释放第二个视频文件
    out.release()  # 释放视频写入对象

    log_message("视频合成完成！\n")

def compress_video(input_path, output_path, bitrate, log_callback, progress_callback):  # 第5步
    # 根据文件扩展名确定编解码器
    extension = os.path.splitext(output_path)[1].lower()  # 获取文件扩展名
    codec = {
        '.mp4': 'libx264',
        '.avi': 'libxvid',
        '.mov': 'libx264',
        '.mkv': 'libx264',
        '.wmv': 'wmv2'
    }.get(extension, 'libx264')  # 默认使用libx264编解码器

    clip = VideoFileClip(input_path)  # 加载输入视频
    total_frames = int(clip.duration * clip.fps)  # 获取视频总帧数
    last_frame_count = -1  # 初始化最后的帧计数
    def update_progress(gf, t):
        nonlocal last_frame_count  # 使用nonlocal声明引用外部变量
        frame_count = int(t * clip.fps)  # 计算当前帧数
        if frame_count != last_frame_count:
            progress_callback(frame_count, total_frames)  # 更新进度条
            if frame_count % int(clip.fps) == 0 or frame_count == total_frames:
                log_callback(f"压缩进度: {frame_count}/{total_frames} 帧")  # 记录压缩进度日志
            last_frame_count = frame_count
        return gf(t)

    new_clip = clip.fl(update_progress)  # 应用进度更新函数
    new_clip.write_videofile(output_path, codec=codec, bitrate=f"{bitrate}k", temp_audiofile=os.path.join(temp_dir, "temp_audiofile.mp3"), remove_temp=True)  # 写入新视频文件，并指定编解码器和临时音频文件路径

    log_callback("视频压缩完成！\n")  # 记录压缩完成日志

def start_processing():  # 第1步
    global temp_dir
    temp_dir = tempfile.mkdtemp()  # 创建临时目录

    # 开始处理视频合成
    video_a_path = video_a_var.get()  # 获取第一个视频路径
    video_b_path = video_b_var.get()  # 获取第二个视频路径
    output_file = output_file_var.get()  # 获取输出文件路径
    compress = compress_var.get()  # 获取是否压缩选项
    bitrate = bitrate_var.get()  # 获取码率

    if not video_a_path or not video_b_path or not output_file:
        messagebox.showerror("错误", "请选择两个视频文件和导出位置！")  # 如果未选择视频或导出位置，显示错误消息
        return

    temp_output_path = os.path.join(temp_dir, "temp_merged_video.mp4").replace("/","\\")  # 构建临时输出文件路径
    log_message("——————任务开始——————\n")  # 记录开始合成日志

    progress_bar['value'] = 0  # 重置进度条
    threading.Thread(target=merge_and_compress, args=(video_a_path, video_b_path, temp_output_path, output_file, compress, bitrate)).start()  # 启动线程执行视频合成和压缩

def merge_and_compress(video_a_path, video_b_path, temp_output_path, final_output_path, compress, bitrate):  # 第2步
    # 合成和压缩视频
    def progress_callback(current_frame, total_frames):
        # 更新进度条回调函数
        progress_percentage = (current_frame / total_frames) * 100  # 计算进度百分比
        progress_bar['value'] = progress_percentage  # 更新进度条

    merge_videos(video_a_path, video_b_path, temp_output_path, progress_callback, log_message)  # 合成视频

    if compress:
        log_message("开始压缩视频......")
        compress_video(temp_output_path, final_output_path, bitrate, log_message, progress_callback)  # 压缩视频
    else:
        shutil.move(temp_output_path, final_output_path)  # 移动临时文件到最终输出路径

    log_message(f"视频处理完成！输出文件: {final_output_path}\n")
    shutil.rmtree(temp_dir)  # 删除临时目录

def log_message(message):
    # 记录日志信息
    log_text.config(state=NORMAL)  # 启用日志文本框
    log_text.insert(END, f"[{time.strftime('%H:%M:%S')}] {message}\n")  # 插入日志信息
    log_text.config(state=DISABLED)  # 禁用日志文本框
    log_text.see(END)  # 滚动到日志文本末尾

def on_compress_checked():
    # 压缩选项复选框状态变化处理函数
    if compress_var.get():
        bitrate_entry.config(state=NORMAL)  # 如果选择压缩，启用码率输入框
    else:
        bitrate_entry.config(state=DISABLED)  # 否则禁用码率输入框


# 初始化Tkinter窗口
root = Tk()
root.title("视频杂交器 —— VideoHybridizer")

#----------高DPI适配start---------

#获得当前的缩放因子
ScaleFactor=round(win32print.GetDeviceCaps(win32gui.GetDC(0), win32con.DESKTOPHORZRES) / win32api.GetSystemMetrics (0), 2)

#调用api设置成由应用程序缩放
try:  # 系统版本 >= win 8.1
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except:  # 系统版本 <= win 8.0
    ctypes.windll.user32.SetProcessDPIAware()

#设置缩放因子
root.tk.call('tk', 'scaling', ScaleFactor/0.75)

#----------高DPI适配end---------

# 配置Tkinter样式
style = Style()
style.configure("TButton", padding=6, relief="flat")  # 配置按钮样式
style.configure("TLabel", padding=6)  # 配置标签样式
style.configure("TEntry", padding=6)  # 配置输入框样式
style.configure("TCheckbutton", padding=6)  # 配置复选框样式
style.configure("TFrame", padding=6)  # 配置框架样式

main_frame = Frame(root)  # 创建主框架
main_frame.pack(padx=20, pady=20, expand=True, fill='both')  # 将主框架放置在窗口中央，并设置内边距

inner_frame = Frame(main_frame)  # 创建内框架，用于居中内容
inner_frame.pack(expand=True)  # 将内框架设置为自动扩展

# 定义Tkinter变量
video_a_var = StringVar()  # 视频A路径变量
video_b_var = StringVar()  # 视频B路径变量
output_file_var = StringVar()  # 输出文件路径变量
compress_var = IntVar()  # 是否压缩变量
bitrate_var = StringVar(value="10000")  # 码率变量

# 添加视频A选择组件
Label(inner_frame, text="视频A").grid(row=0, column=0, padx=10, pady=5)  # 添加标签
Entry(inner_frame, textvariable=video_a_var, width=50).grid(row=0, column=1, padx=10, pady=5)  # 添加输入框
Button(inner_frame, text="选择", command=lambda: select_video(video_a_var)).grid(row=0, column=2, padx=10, pady=5)  # 添加按钮

# 添加视频B选择组件
Label(inner_frame, text="视频B").grid(row=1, column=0, padx=10, pady=5)  # 添加标签
Entry(inner_frame, textvariable=video_b_var, width=50).grid(row=1, column=1, padx=10, pady=5)  # 添加输入框
Button(inner_frame, text="选择", command=lambda: select_video(video_b_var)).grid(row=1, column=2, padx=10, pady=5)  # 添加按钮

# 添加输出文件选择组件
Label(inner_frame, text="导出位置").grid(row=2, column=0, padx=10, pady=5)  # 添加标签
Entry(inner_frame, textvariable=output_file_var, width=50).grid(row=2, column=1, padx=10, pady=5)  # 添加输入框
Button(inner_frame, text="选择", command=lambda: select_output_file(output_file_var)).grid(row=2, column=2, padx=10, pady=5)  # 添加按钮

# 添加压缩选项和码率输入组件
compress_frame = Frame(inner_frame)  # 创建压缩选项框架
compress_frame.grid(row=3, column=0, columnspan=3, padx=10, pady=5, sticky='w')  # 添加压缩选项框架到网格
Checkbutton(compress_frame, text="压缩视频", variable=compress_var, command=on_compress_checked).pack(side='left')  # 添加复选框
Label(compress_frame, text="码率 (kbps)").pack(side='left', padx=10)  # 添加标签
bitrate_entry = Entry(compress_frame, textvariable=bitrate_var, width=20)  # 添加输入框
bitrate_entry.pack(side='left')  # 放置输入框
bitrate_entry.config(state=DISABLED)  # 初始状态禁用

prompt = '''\
⭐帧排列图解：
视频A (60 FPS):  A₁ - A₂ - A₃ - A₄ - A₅ - ...
视频B (60 FPS):  B₁ - B₂ - B₃ - B₄ - B₅ - ...
合成视频 (120 FPS): A₁ - B₁ - A₂ - B₂ - A₃ - B₃ - A₄ - B₄ - ...
⭐实测：
视频A：哔哩哔哩大会员60FPS的画面（60FPS保留每四帧的1、3帧）
视频B：哔哩哔哩30FPS的画面（30FPS保留每四帧的第2帧）
⭐提示：
1、待处理的两个视频应该保证分辨率大小相同
2、若待处理的两个视频长度不相同，合成的视频将自动舍弃较长视频的剩余部分
3、点击“开始合成”后，程序会把两个视频转换为60帧，然后使用间隔放帧的策略，合成出120帧的视频
4、合成后的视频无音轨，如有需要，请自行添加音轨'''
# 添加使用提示
usage_label = Label(inner_frame, text=prompt, foreground="#0070C0")
usage_label.grid(row=4, column=0, columnspan=3, padx=10, pady=10)  # 设置使用提示，并放置到网格

# 添加开始合成按钮
Button(inner_frame, text="开始合成", command=start_processing).grid(row=5, column=0, columnspan=3, pady=20)  # 添加按钮并放置到网格

# 添加进度条
progress_bar = Progressbar(inner_frame, orient='horizontal', length=500, mode='determinate')  # 创建进度条
progress_bar.grid(row=6, column=0, columnspan=3, pady=10)  # 将进度条放置到网格

# 添加日志文本框
log_text = Text(inner_frame, wrap='word', height=20, state=DISABLED)  # 创建日志文本框
log_text.grid(row=7, column=0, columnspan=3, padx=10, pady=10)  # 将日志文本框放置到网格
scrollbar = Scrollbar(inner_frame, command=log_text.yview)  # 创建滚动条
scrollbar.grid(row=7, column=3, sticky='nsew')  # 将滚动条放置到网格
log_text['yscrollcommand'] = scrollbar.set  # 设置滚动条与文本框的关联

# 自动调整窗口大小以适应内容
root.update_idletasks()  # 更新窗口内容
root.minsize(main_frame.winfo_width() + 40, main_frame.winfo_height() + 40)  # 设置窗口最小尺寸
root.mainloop()  # 启动Tkinter主循环
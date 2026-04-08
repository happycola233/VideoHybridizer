import os
import threading
import time
import subprocess
import re
import json
from tkinter import Checkbutton, IntVar, Tk, Text, Scrollbar, END, DISABLED, NORMAL, Frame, StringVar
from tkinter import filedialog, messagebox
from tkinter.ttk import Button, Label, Entry, Progressbar, Style, Combobox
import ctypes, win32api, win32con, win32gui, win32print
import queue
import sys

def resource_path(relative_path):
    """获取资源文件的绝对路径，支持开发和 PyInstaller 环境"""
    try:
        base_path = sys._MEIPASS  # PyInstaller 临时目录
    except AttributeError:
        base_path = os.path.dirname(__file__)  # 开发环境使用脚本目录
    return os.path.join(base_path, relative_path)

def check_ffmpeg():
    """检查 FFmpeg 是否安装并支持所需编码器"""
    ffmpeg_path = os.path.join(resource_path("ffmpeg"), "ffmpeg.exe")
    ffprobe_path = os.path.join(resource_path("ffmpeg"), "ffprobe.exe")
    
    if os.path.exists(ffmpeg_path) and os.path.exists(ffprobe_path):
        os.environ["PATH"] = os.path.dirname(ffmpeg_path) + os.pathsep + os.environ.get("PATH", "")
    else:
        ffmpeg_path = "ffmpeg"
        ffprobe_path = "ffprobe"
    
    try:
        result = subprocess.run(
            [ffmpeg_path, "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
            creationflags=0x08000000
        )
        encoders = result.stdout
        required_encoders = ["h264_nvenc", "hevc_nvenc", "h264_amf", "hevc_amf", "h264_qsv", "hevc_qsv", "libx264", "libx265"]
        missing_encoders = [enc for enc in required_encoders if enc not in encoders]
        if missing_encoders:
            messagebox.showerror("错误", f"FFmpeg 缺少支持的编码器：{', '.join(missing_encoders)}！请确保安装支持 NVENC/AMF/QSV 和 libx264/libx265 的 FFmpeg。")
            return False
        subprocess.run(
            [ffmpeg_path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            creationflags=0x08000000
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        messagebox.showerror("错误", "FFmpeg 未安装或未配置环境变量！请安装 FFmpeg 并确保其在系统 PATH 中，或确保程序目录包含 ffmpeg.exe 和 ffprobe.exe。")
        return False

def select_video(label_var):
    """弹出文件选择对话框，让用户选择视频文件"""
    file_path = filedialog.askopenfilename(
        title="选择视频文件",
        filetypes=[("视频文件", "*.MP4 *.AVI *.MOV *.MKV *.WMV"), ("所有文件", "*.*")]
    ).replace("/", "\\")
    if file_path:
        label_var.set(file_path)
    return None

def select_output_file(label_var):
    """弹出文件保存对话框，让用户选择输出文件位置和文件名"""
    file_path = filedialog.asksaveasfilename(
        title="选择导出位置和文件名",
        defaultextension=".mp4",
        filetypes=[("MP4文件", "*.mp4"), ("AVI文件", "*.avi"), ("MOV文件", "*.mov"), ("MKV文件", "*.mkv"), ("WMV文件", "*.wmv"), ("所有文件", "*.*")]
    ).replace("/", "\\")
    if file_path:
        label_var.set(file_path)
    return None

def get_video_info(video_path, log_callback):
    """获取视频的分辨率、帧率、帧数和 SAR"""
    if not os.path.exists(video_path):
        log_callback(f"错误：视频文件不存在: {video_path}")
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames,codec_type,sample_aspect_ratio",
        "-of", "json", video_path
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace',
            creationflags=0x08000000
        )
        data = json.loads(result.stdout)
        if not data.get("streams"):
            log_callback(f"错误：视频 {video_path} 没有有效的视频流")
            raise ValueError(f"No valid video stream in {video_path}")
        stream = data["streams"][0]
        if stream.get("codec_type") != "video":
            log_callback(f"错误：选择的流不是视频流: {video_path}")
            raise ValueError(f"Selected stream is not video in {video_path}")

        width = stream.get("width")
        height = stream.get("height")
        if width is None or height is None:
            log_callback(f"错误：无法获取视频 {video_path} 的分辨率")
            raise ValueError(f"Missing width or height in {video_path}")

        frame_rate = stream.get("r_frame_rate", "0/1")
        try:
            num, denom = map(int, frame_rate.split('/'))
            fps = num / denom if denom != 0 else 0
        except (ValueError, ZeroDivisionError):
            log_callback(f"错误：无法解析视频 {video_path} 的帧率: {frame_rate}")
            raise ValueError(f"Invalid frame rate in {video_path}")

        nb_frames = stream.get("nb_frames")
        if nb_frames is None:
            cmd_duration = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "json", video_path
            ]
            result_duration = subprocess.run(
                cmd_duration, capture_output=True, text=True, check=True, encoding='utf-8'
            )
            duration = json.loads(result_duration.stdout).get("format", {}).get("duration")
            if duration:
                nb_frames = int(float(duration) * fps)
            else:
                log_callback(f"错误：无法获取视频 {video_path} 的帧数")
                raise ValueError(f"Missing nb_frames in {video_path}")

        sar = stream.get("sample_aspect_ratio", "1:1")
        return width, height, fps, int(nb_frames), sar
    except subprocess.CalledProcessError as e:
        log_callback(f"ffprobe 失败: {e.stderr}")
        raise RuntimeError(f"ffprobe failed for {video_path}: {e.stderr}")
    except json.JSONDecodeError as e:
        log_callback(f"ffprobe 输出解析失败: {result.stdout}")
        raise RuntimeError(f"Invalid JSON output from ffprobe: {e}")

def get_duration(video_path, log_callback):
    """获取视频时长（秒）"""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace',
            creationflags=0x08000000
        )
        data = json.loads(result.stdout)
        duration = data.get("format", {}).get("duration")
        if duration is None:
            log_callback(f"错误：无法获取视频 {video_path} 的时长")
            raise ValueError(f"Missing duration in {video_path}")
        return float(duration)
    except subprocess.CalledProcessError as e:
        log_callback(f"ffprobe 失败: {e.stderr}")
        raise RuntimeError(f"ffprobe failed for {video_path}: {e.stderr}")
    except json.JSONDecodeError as e:
        log_callback(f"ffprobe 输出解析失败: {result.stdout}")
        raise RuntimeError(f"Invalid JSON output from ffprobe: {e}")

def convert_to_60fps(video_path, output_path, log_callback, progress_callback, hwaccel_type, codec):
    """使用指定编码器将视频转换为 60 FPS"""
    log_callback(f"转换视频 {video_path} 到 60 FPS（{hwaccel_type if hwaccel_type != '禁用' else '软件编码'}, {codec}）...")

    duration = get_duration(video_path, log_callback)
    total_frames = int(duration * 60)
    log_callback(f"目标帧数 (60 FPS): {total_frames}")

    cmd = ["ffmpeg", "-y"]
    if hwaccel_type == "NVIDIA NVENC":
        cmd.extend(["-hwaccel", "nvdec"])
    elif hwaccel_type == "AMD AMF":
        cmd.extend(["-hwaccel", "d3d11va"])
    elif hwaccel_type == "Intel QSV":
        cmd.extend([])
    cmd.extend([
        "-i", video_path,
        "-vf", "fps=60,setsar=1,setpts=PTS-STARTPTS", 
        "-r", "60", 
        "-start_at_zero", 
        ])

    if hwaccel_type == "NVIDIA NVENC":
        cmd.extend([
            "-c:v", "h264_nvenc" if codec == "H.264" else "hevc_nvenc",
            "-preset", "p7", 
            "-rc", "vbr", 
            "-cq", "18", 
            "-rc-lookahead", "60"
        ])
    elif hwaccel_type == "AMD AMF":
        cmd.extend([
            "-c:v", "h264_amf" if codec == "H.264" else "hevc_amf",
            "-usage", "transcoding", 
            "-quality", "quality", 
            "-rc", "cqp",
            "-qp_i", "16",
            "-qp_p", "16",
            "-qp_b", "18",
        ])
    elif hwaccel_type == "Intel QSV":
        cmd.extend([
            "-c:v", "h264_qsv" if codec == "H.264" else "hevc_qsv",
            "-rc", "vbr", 
            "-global_quality", "18", 
        ])
    else:  # 软件编码
        cmd.extend(["-c:v", "libx264" if codec == "H.264" else "libx265", 
                    "-preset", "slow", "-crf", "18"])

    cmd.extend(["-c:a", "copy", "-pix_fmt", "yuv420p", output_path])
    
    timeout_duration = 300 # 5分钟超时

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=0x08000000
        )
        stderr_queue = queue.Queue()
        stdout_queue = queue.Queue()

        def read_stderr():
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                stderr_queue.put(line.strip())

        def read_stdout():
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                stdout_queue.put(line.strip())

        stderr_thread = threading.Thread(target=read_stderr)
        stdout_thread = threading.Thread(target=read_stdout)
        stderr_thread.start()
        stdout_thread.start()

        current_frame = 0
        start_time = time.time()
        last_log_time = time.time()
        ffmpeg_keywords = ["frame=", "speed=", "time=", "bitrate="]

        while process.poll() is None:
            try:
                while not stderr_queue.empty():
                    line = stderr_queue.get_nowait()
                    if any(keyword in line for keyword in ffmpeg_keywords):
                        # 如果有FFmpeg进度输出，重置超时时间
                        start_time = time.time() 
                        match = re.search(r"frame=\s*(\d+)", line)
                        if match:
                            current_frame = int(match.group(1))
                            progress_percent = min((current_frame / total_frames) * 100, 100)
                            progress_callback(current_frame, total_frames, "转换")
                            if time.time() - last_log_time > 1.0 or current_frame % 120 == 0:
                                speed_match = re.search(r"speed=\s*([\d.]+)x", line)
                                speed = speed_match.group(1) if speed_match else "1.0"
                                log_callback(f"进度: {current_frame}/{total_frames} 帧 | {progress_percent:.1f}% | 速度: {speed}x")
                                last_log_time = time.time()
            except queue.Empty:
                pass
            if time.time() - start_time > timeout_duration:
                log_callback(f"FFmpeg 进程超时（{timeout_duration/60} 分钟无输出），正在终止...")
                process.terminate()
                # 等待进程结束，如果仍未结束，则强制杀死
                try:
                    process.wait(timeout=5) 
                except subprocess.TimeoutExpired:
                    process.kill()
                    log_callback("FFmpeg 进程被强制终止。")
                raise TimeoutError("FFmpeg 进程超时")
            time.sleep(0.01)

        stderr_thread.join()
        stdout_thread.join()

        while not stderr_queue.empty():
            line = stderr_queue.get()
            if any(keyword in line for keyword in ffmpeg_keywords):
                match = re.search(r"frame=\s*(\d+)", line)
                if match:
                    current_frame = int(match.group(1))
                    progress_percent = min((current_frame / total_frames) * 100, 100)
                    progress_callback(current_frame, total_frames, "转换")
                    if time.time() - last_log_time > 1.0 or current_frame % 120 == 0:
                        speed_match = re.search(r"speed=\s*([\d.]+)x", line)
                        speed = speed_match.group(1) if speed_match else "1.0"
                        log_callback(f"进度: {current_frame}/{total_frames} 帧 | {progress_percent:.1f}% | 速度: {speed}x")
                        last_log_time = time.time()

        while not stdout_queue.empty():
            stdout_queue.get()  # 清空 stdout，不显示

        if process.returncode != 0:
            log_callback(f"FFmpeg 转换失败，退出码: {process.returncode}")
            raise subprocess.CalledProcessError(process.returncode, cmd)
        log_callback(f"转换完成: {output_path}")
        return output_path
    except subprocess.CalledProcessError as e:
        log_callback(f"FFmpeg 转换失败: {e.stderr}")
        raise
    except TimeoutError as e:
        log_callback(f"错误: {str(e)}")
        raise

def merge_videos(video_a_path, video_b_path, output_path, progress_callback, log_callback, hwaccel_type, codec):
    """使用指定编码器合并两个 60 FPS 视频为 120 FPS，帧交替 A₁B₁A₂B₂"""
    log_callback(f"开始合并视频为 120 帧（{hwaccel_type if hwaccel_type != '禁用' else '软件编码'}, {codec}）...")

    log_callback(f"检查视频 A: {video_a_path}")
    w1, h1, fps_a, frames_a, sar_a = get_video_info(video_a_path, log_callback)
    duration_a = get_duration(video_a_path, log_callback)
    log_callback(f"视频 A: {w1}x{h1}@{fps_a:.2f}FPS, 时长: {duration_a:.2f}s")
    
    log_callback(f"检查视频 B: {video_b_path}")
    w2, h2, fps_b, frames_b, sar_b = get_video_info(video_b_path, log_callback)
    duration_b = get_duration(video_b_path, log_callback)
    log_callback(f"视频 B: {w2}x{h2}@{fps_b:.2f}FPS, 时长: {duration_b:.2f}s")

    if (w1, h1) != (w2, h2):
        log_callback(f"错误：视频分辨率不一致！视频 A: {w1}x{h1}, 视频 B: {w2}x{h2}")
        raise ValueError("Resolution mismatch")

    temp_a_path = video_a_path
    temp_b_path = video_b_path
    if abs(fps_a - 60.0) > 0.1:
        temp_a_path = os.path.splitext(video_a_path)[0] + "_60fps.mp4"
        log_callback(f"转换视频 A 到 60FPS...")
        convert_to_60fps(video_a_path, temp_a_path, log_callback, progress_callback, hwaccel_type, codec)
    if abs(fps_b - 60.0) > 0.1:
        temp_b_path = os.path.splitext(video_b_path)[0] + "_60fps.mp4"
        log_callback(f"转换视频 B 到 60FPS...")
        convert_to_60fps(video_b_path, temp_b_path, log_callback, progress_callback, hwaccel_type, codec)

    target_duration = min(duration_a, duration_b)
    total_frames_120fps = int(target_duration * 120)
    log_callback(f"开始合并，目标总帧数: {total_frames_120fps} (120FPS)")

    ffmpeg_cmd = ["ffmpeg", "-y"]
    if hwaccel_type == "NVIDIA NVENC":
        ffmpeg_cmd.extend(["-hwaccel", "nvdec"])
    elif hwaccel_type == "AMD AMF":
        ffmpeg_cmd.extend(["-hwaccel", "d3d11va"])
    elif hwaccel_type == "Intel QSV":
        ffmpeg_cmd.extend([])
    ffmpeg_cmd.extend([
        "-i", temp_a_path,
        "-i", temp_b_path,
        "-i", video_a_path,
        "-filter_complex",
        "[0:v]fps=60,setsar=1,setpts=PTS-STARTPTS[v0];[1:v]fps=60,setsar=1,setpts=PTS-STARTPTS[v1];[v0][v1]interleave[v]",
        "-map", "[v]",
        "-map", "2:a?",
        "-r", "120",
        "-start_at_zero",
        "-async", "1"
    ])

    # 应用码率（如果启用）
    if bitrate_enabled_var.get() == 1:
        try:
            bitrate_mbps = float(bitrate_var.get())
            bitrate_kbps = int(bitrate_mbps * 1000)  # 将 Mbps 转换为 kbps
            ffmpeg_cmd.extend(["-b:v", f"{bitrate_kbps}k"])
        except ValueError:
            log_callback("错误：请输入有效的码率（数字，单位 Mbps）")
            return False


    # 设置编码器和参数
    if hwaccel_type == "NVIDIA NVENC":
        ffmpeg_cmd.extend([
            "-c:v", "h264_nvenc" if codec == "H.264" else "hevc_nvenc", 
            "-preset", "p7"
            ])
        if bitrate_enabled_var.get() == 1:
            ffmpeg_cmd.extend([
                "-rc", "vbr", 
                "-b:v", f"{bitrate_kbps}k"
                ])
        else:
            ffmpeg_cmd.extend([
                "-rc", "vbr", 
                "-cq", "18", 
                "-rc-lookahead", "60"
                ])

    elif hwaccel_type == "AMD AMF":
        ffmpeg_cmd.extend([
            "-c:v", "h264_amf" if codec == "H.264" else "hevc_amf", 
            "-usage", "transcoding", 
            "-quality", "quality", 
            ])
        if bitrate_enabled_var.get() == 1:
            ffmpeg_cmd.extend([
                "-b:v", f"{bitrate_kbps}k"
                ])
        else:
            ffmpeg_cmd.extend([
                "-rc", "cqp",
                "-qp_i", "16",
                "-qp_p", "16",
                "-qp_b", "18",
                "-refs", "4"
            ])

    elif hwaccel_type == "Intel QSV":
        ffmpeg_cmd.extend([
            "-c:v", "h264_qsv" if codec == "H.264" else "hevc_qsv",
            "-preset", "fast",
        ])
        if bitrate_enabled_var.get() == 1:
            ffmpeg_cmd.extend([
                "-rc", "vbr", 
                "-b:v", f"{bitrate_kbps}k"
            ])
        else:
            ffmpeg_cmd.extend([
                "-rc", "vbr", 
                "-global_quality", "18", 
            ])
            

    else:  # 软编码
        ffmpeg_cmd.extend([
            "-c:v", "libx264" if codec == "H.264" else 
            "libx265", "-preset", "slow"])
        if bitrate_enabled_var.get() == 1:
            ffmpeg_cmd.extend(["-b:v", f"{bitrate_kbps}k"])
        else:
            ffmpeg_cmd.extend(["-crf", "18"])

    ffmpeg_cmd.extend(["-pix_fmt", "yuv420p", "-c:a", "aac", output_path])

    timeout_duration = 300 # 5分钟超时

    process = subprocess.Popen(
        ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, encoding='utf-8', errors='replace', bufsize=1,
        creationflags=0x08000000
    )

    stderr_queue = queue.Queue()
    stdout_queue = queue.Queue()

    def read_stderr():
        while True:
            line = process.stderr.readline()
            if not line:
                break
            stderr_queue.put(line.strip())

    def read_stdout():
        while True:
            line = process.stdout.readline()
            if not line:
                break
            stdout_queue.put(line.strip())

    stderr_thread = threading.Thread(target=read_stderr)
    stdout_thread = threading.Thread(target=read_stdout)
    stderr_thread.start()
    stdout_thread.start()

    current_frame = 0
    start_time = time.time()
    last_log_time = time.time()
    ffmpeg_keywords = ["frame=", "speed=", "time=", "bitrate="]

    while process.poll() is None:
        try:
            while not stderr_queue.empty():
                line = stderr_queue.get_nowait()
                if any(keyword in line for keyword in ffmpeg_keywords):
                    # 如果有FFmpeg进度输出，重置超时时间
                    start_time = time.time()
                    match = re.search(r"frame=\s*(\d+)", line)
                    if match:
                        current_frame = int(match.group(1))
                        progress_percent = min((current_frame / total_frames_120fps) * 100, 100)
                        progress_callback(current_frame, total_frames_120fps, "合并")
                        if time.time() - last_log_time > 1.0 or current_frame % 120 == 0:
                            speed_match = re.search(r"speed=\s*([\d.]+)x", line)
                            speed = speed_match.group(1) if speed_match else "1.0"
                            log_callback(f"进度: {current_frame}/{total_frames_120fps} 帧 | {progress_percent:.1f}% | 速度: {speed}x")
                            last_log_time = time.time()
        except queue.Empty:
            pass
        if time.time() - start_time > timeout_duration:
            log_callback(f"FFmpeg 进程超时（{timeout_duration/60} 分钟无输出），正在终止...")
            process.terminate()
            # 等待进程结束，如果仍未结束，则强制杀死
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                log_callback("FFmpeg 进程被强制终止。")
            raise TimeoutError("处理超时")
        time.sleep(0.01)

    stderr_thread.join()
    stdout_thread.join()

    while not stderr_queue.empty():
        line = stderr_queue.get()
        if any(keyword in line for keyword in ffmpeg_keywords):
            match = re.search(r"frame=\s*(\d+)", line)
            if match:
                current_frame = int(match.group(1))
                progress_percent = min((current_frame / total_frames_120fps) * 100, 100)
                progress_callback(current_frame, total_frames_120fps, "合并")
                if time.time() - last_log_time > 1.0 or current_frame % 120 == 0:
                    speed_match = re.search(r"speed=\s*([\d.]+)x", line)
                    speed = speed_match.group(1) if speed_match else "1.0"
                    log_callback(f"进度: {current_frame}/{total_frames_120fps} 帧 | {progress_percent:.1f}% | 速度: {speed}x")
                    last_log_time = time.time()

    while not stdout_queue.empty():
        stdout_queue.get()  # 清空 stdout，不显示

    if process.returncode != 0:
        log_callback(f"处理失败，错误码: {process.returncode}")
        raise subprocess.CalledProcessError(process.returncode, ffmpeg_cmd)

    if temp_a_path != video_a_path:
        try:
            os.remove(temp_a_path)
            log_callback(f"已删除临时文件: {temp_a_path}")
        except Exception as e:
            log_callback(f"删除临时文件失败: {str(e)}")
    
    if temp_b_path != video_b_path:
        try:
            os.remove(temp_b_path)
            log_callback(f"已删除临时文件: {temp_b_path}")
        except Exception as e:
            log_callback(f"删除临时文件失败: {str(e)}")

    log_callback(f"视频合并完成: {output_path}")

def start_processing():
    """开始处理视频合成"""
    if not check_ffmpeg():
        return

    video_a_path = video_a_var.get()
    video_b_path = video_b_var.get()
    output_file = output_file_var.get()
    hwaccel_type = hwaccel_var.get()
    codec = codec_var.get()

    if not video_a_path or not video_b_path or not output_file:
        messagebox.showerror("错误", "请选择两个视频文件和导出位置！")
        return

    if not os.path.exists(video_a_path):
        messagebox.showerror("错误", f"视频 A 文件不存在: {video_a_path}")
        return
    if not os.path.exists(video_b_path):
        messagebox.showerror("错误", f"视频 B 文件不存在: {video_b_path}")
        return

    log_message("——————任务开始——————\n")
    log_message(f"视频 A: {video_a_path}")
    log_message(f"视频 B: {video_b_path}")
    log_message(f"输出文件: {output_file}")
    log_message(f"编码设置: {hwaccel_type if hwaccel_type != '禁用' else '软件编码'}, {codec}")
    progress_bar['value'] = 0
    threading.Thread(target=merge_and_compress, args=(video_a_path, video_b_path, output_file, hwaccel_type, codec)).start()

def merge_and_compress(video_a_path, video_b_path, final_output_path, hwaccel_type, codec):
    """合成视频"""
    def progress_callback(current_frame, total_frames, stage):
        progress_percentage = min((current_frame / total_frames) * 100, 100)
        progress_bar['value'] = progress_percentage
        root.update_idletasks()

    try:
        merge_videos(video_a_path, video_b_path, final_output_path, progress_callback, log_message, hwaccel_type, codec)
        log_message(f"视频处理完成！输出文件: {final_output_path}\n")
    except Exception as e:
        log_message(f"处理失败: {str(e)}\n")
        messagebox.showerror("错误", f"视频处理失败: {str(e)}")

def log_message(message):
    """记录日志信息"""
    log_text.config(state=NORMAL)
    log_text.insert(END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
    log_text.config(state=DISABLED)
    log_text.see(END)
    root.update_idletasks()

root = Tk()
root.title("视频杂交器 —— VideoHybridizer")

# 设置窗口图标
try:
    root.iconbitmap(resource_path('icon.ico'))
except Exception as e:
    print(f"设置窗口图标失败: {e}")

ScaleFactor = round(win32print.GetDeviceCaps(win32gui.GetDC(0), win32con.DESKTOPHORZRES) / win32api.GetSystemMetrics(0), 2)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except:
    ctypes.windll.user32.SetProcessDPIAware()
root.tk.call('tk', 'scaling', ScaleFactor/0.75)

whnd = ctypes.windll.kernel32.GetConsoleWindow()
if whnd != 0:
    ctypes.windll.user32.ShowWindow(whnd, 6)

style = Style()
style.configure("TButton", padding=6, relief="flat")
style.configure("TLabel", padding=6)
style.configure("TEntry", padding=6)
style.configure("TFrame", padding=6)
style.configure("TCombobox", padding=6)

main_frame = Frame(root)
main_frame.pack(padx=20, pady=20, expand=True, fill='both')

inner_frame = Frame(main_frame)
inner_frame.pack(expand=True)

def toggle_bitrate_entry():
    if bitrate_enabled_var.get() == 1:
        bitrate_entry.config(state=NORMAL)
    else:
        bitrate_entry.config(state=DISABLED)

video_a_var = StringVar()
video_b_var = StringVar()
output_file_var = StringVar()
hwaccel_var = StringVar(value="NVIDIA NVENC")
codec_var = StringVar(value="H.264")
drate_enabled_var = IntVar(value=0)
bitrate_enabled_var = IntVar(value=0)
bitrate_var = StringVar(value="5")

Label(inner_frame, text="视频 A").grid(row=0, column=0, padx=10, pady=5)
Entry(inner_frame, textvariable=video_a_var, width=50).grid(row=0, column=1, padx=10, pady=5)
Button(inner_frame, text="选择", command=lambda: select_video(video_a_var)).grid(row=0, column=2, padx=10, pady=5)

Label(inner_frame, text="视频 B").grid(row=1, column=0, padx=10, pady=5)
Entry(inner_frame, textvariable=video_b_var, width=50).grid(row=1, column=1, padx=10, pady=5)
Button(inner_frame, text="选择", command=lambda: select_video(video_b_var)).grid(row=1, column=2, padx=10, pady=5)

Label(inner_frame, text="导出位置").grid(row=2, column=0, padx=10, pady=5)
Entry(inner_frame, textvariable=output_file_var, width=50).grid(row=2, column=1, padx=10, pady=5)
Button(inner_frame, text="选择", command=lambda: select_output_file(output_file_var)).grid(row=2, column=2, padx=10, pady=5)

Label(inner_frame, text="硬件加速").grid(row=3, column=0, padx=10, pady=5)
Combobox(inner_frame, textvariable=hwaccel_var, values=["NVIDIA NVENC", "AMD AMF", "Intel QSV", "禁用"], state="readonly", width=12).grid(row=3, column=1, padx=(10, 5), pady=5, sticky="w")
Label(inner_frame, text="编码格式").grid(row=3, column=1, padx=(140, 5), pady=5, sticky="w")
Combobox(inner_frame, textvariable=codec_var, values=["H.264", "H.265"], state="readonly", width=12).grid(row=3, column=1, padx=(210, 5), pady=5, sticky="w")

Checkbutton(inner_frame, text="码率", variable=bitrate_enabled_var, command=toggle_bitrate_entry).grid(row=3, column=2, padx=(0, 5), pady=5, sticky="w")

bitrate_entry = Entry(inner_frame, textvariable=bitrate_var, width=5)
bitrate_entry.grid(row=3, column=2, padx=(50, 5), pady=5, sticky="w")
bitrate_entry.config(state=DISABLED if bitrate_enabled_var.get() == 0 else NORMAL)

Label(inner_frame, text="Mbps").grid(row=3, column=2, padx=(75, 10), pady=5, sticky="w")

prompt = '''\
⭐ 帧排列图解：
视频 A (60 FPS):  A₁ - A₂ - A₃ - A₄ - A₅ - ...
视频 B (60 FPS):  B₁ - B₂ - B₃ - B₄ - B₅ - ...
合成视频 (120 FPS): A₁ - B₁ - A₂ - B₂ - A₃ - B₃ - A₄ - B₄ - ...
⭐ 实测：
视频 A：哔哩哔哩大会员 60FPS 的画面（60FPS 保留每四帧的 1、3 帧）
视频 B：哔哩哔哩 30FPS 的画面（30FPS 保留每四帧的第 2 帧）
⭐ 提示：
1、待处理的两个视频必须保证分辨率大小相同
2、若待处理的两个视频长度不相同，合成的视频将自动舍弃较长视频的剩余部分
3、点击“开始合成”后，程序会检查帧率并转换为 60 帧，然后合成 120 帧的视频
4、输出视频包含视频 A 的音轨
5、硬件加速需对应 GPU 支持'''
usage_label = Label(inner_frame, text=prompt, foreground="#0070C0")
usage_label.grid(row=4, column=0, columnspan=3, padx=10, pady=10)

Button(inner_frame, text="开始合成", command=start_processing).grid(row=5, column=0, columnspan=3, pady=20)

progress_bar = Progressbar(inner_frame, orient='horizontal', length=500, mode='determinate')
progress_bar.grid(row=6, column=0, columnspan=3, pady=10)

log_text = Text(inner_frame, wrap='word', height=20, state=DISABLED)
log_text.grid(row=7, column=0, columnspan=3, padx=10, pady=10)
scrollbar = Scrollbar(inner_frame, command=log_text.yview)
scrollbar.grid(row=7, column=3, sticky='nsew')
log_text['yscrollcommand'] = scrollbar.set

root.update_idletasks()
root.minsize(main_frame.winfo_width() + 40, main_frame.winfo_height() + 40)
root.mainloop()
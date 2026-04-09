import os
import threading
import time
import subprocess
import re
import json
from tkinter import Canvas, IntVar, Tk, Text, END, DISABLED, NORMAL, StringVar, Toplevel
from tkinter import filedialog, messagebox
from tkinter.ttk import Button, Checkbutton, Combobox, Entry, Frame, Label, LabelFrame, Progressbar, Scrollbar, Style
import ctypes, win32api, win32con, win32gui, win32print
import queue
import sys
import tempfile
import tkinter.font as tkfont

WINDOWS_NO_WINDOW = 0x08000000

VIDEO_ENCODERS = {
    ("NVIDIA NVENC", "H.264"): "h264_nvenc",
    ("NVIDIA NVENC", "H.265"): "hevc_nvenc",
    ("AMD AMF", "H.264"): "h264_amf",
    ("AMD AMF", "H.265"): "hevc_amf",
    ("Intel QSV", "H.264"): "h264_qsv",
    ("Intel QSV", "H.265"): "hevc_qsv",
    ("禁用", "H.264"): "libx264",
    ("禁用", "H.265"): "libx265",
}

HWACCEL_ARGS = {
    "NVIDIA NVENC": ["-hwaccel", "nvdec"],
    "AMD AMF": ["-hwaccel", "d3d11va"],
    "Intel QSV": [],
    "禁用": [],
}

ui_queue = queue.Queue()
usage_window = None

def resource_path(relative_path):
    """获取资源文件的绝对路径，支持开发和 PyInstaller 环境"""
    try:
        base_path = sys._MEIPASS  # PyInstaller 临时目录
    except AttributeError:
        base_path = os.path.dirname(__file__)  # 开发环境使用脚本目录
    return os.path.join(base_path, relative_path)

def set_window_icon(window):
    try:
        window.iconbitmap(resource_path('icon.ico'))
    except Exception as e:
        print(f"设置窗口图标失败: {e}")

def run_on_ui_thread(callback, *args, **kwargs):
    if threading.current_thread() is threading.main_thread():
        callback(*args, **kwargs)
    else:
        ui_queue.put((callback, args, kwargs))

def process_ui_queue():
    while True:
        try:
            callback, args, kwargs = ui_queue.get_nowait()
        except queue.Empty:
            break
        callback(*args, **kwargs)
    root.after(50, process_ui_queue)

def get_video_encoder(hwaccel_type, codec):
    return VIDEO_ENCODERS[(hwaccel_type, codec)]

def get_hwaccel_args(hwaccel_type):
    return list(HWACCEL_ARGS.get(hwaccel_type, []))

def configure_ffmpeg_paths():
    ffmpeg_path = os.path.join(resource_path("ffmpeg"), "ffmpeg.exe")
    ffprobe_path = os.path.join(resource_path("ffmpeg"), "ffprobe.exe")

    if os.path.exists(ffmpeg_path) and os.path.exists(ffprobe_path):
        os.environ["PATH"] = os.path.dirname(ffmpeg_path) + os.pathsep + os.environ.get("PATH", "")
    else:
        ffmpeg_path = "ffmpeg"
        ffprobe_path = "ffprobe"

    return ffmpeg_path, ffprobe_path

def check_ffmpeg(hwaccel_type, codec):
    """检查 FFmpeg/FFprobe 是否可用，并确认当前所选编码器存在"""
    ffmpeg_path, ffprobe_path = configure_ffmpeg_paths()

    try:
        subprocess.run(
            [ffprobe_path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            creationflags=WINDOWS_NO_WINDOW
        )
        result = subprocess.run(
            [ffmpeg_path, "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', check=True,
            creationflags=WINDOWS_NO_WINDOW
        )
        encoders = result.stdout
        required_encoder = get_video_encoder(hwaccel_type, codec)
        if required_encoder not in encoders:
            messagebox.showerror("错误", f"当前编码设置不可用：{hwaccel_type} / {codec} 需要 FFmpeg 编码器 {required_encoder}。")
            return False
        subprocess.run(
            [ffmpeg_path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            creationflags=WINDOWS_NO_WINDOW
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

def parse_frame_rate(frame_rate):
    try:
        num, denom = map(int, frame_rate.split('/'))
        if denom == 0:
            return 0.0
        return num / denom
    except (AttributeError, ValueError, ZeroDivisionError):
        return 0.0

def count_video_frames(video_path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace',
        creationflags=WINDOWS_NO_WINDOW
    )
    value = result.stdout.strip()
    return int(value) if value else None

def probe_media_info(video_path, log_callback):
    """获取媒体的核心信息，优先以视频流本身的长度为准"""
    if not os.path.exists(video_path):
        log_callback(f"错误：视频文件不存在: {video_path}")
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate,nb_frames,duration,sample_aspect_ratio:format=duration",
        "-of", "json", video_path
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace',
            creationflags=WINDOWS_NO_WINDOW
        )
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            log_callback(f"错误：视频 {video_path} 没有有效的媒体流")
            raise ValueError(f"No valid streams in {video_path}")

        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if video_stream is None:
            log_callback(f"错误：视频 {video_path} 没有有效的视频流")
            raise ValueError(f"No valid video stream in {video_path}")

        width = video_stream.get("width")
        height = video_stream.get("height")
        if width is None or height is None:
            log_callback(f"错误：无法获取视频 {video_path} 的分辨率")
            raise ValueError(f"Missing width or height in {video_path}")

        fps = parse_frame_rate(video_stream.get("avg_frame_rate", "0/1"))
        if fps <= 0:
            fps = parse_frame_rate(video_stream.get("r_frame_rate", "0/1"))
        if fps <= 0:
            log_callback(f"错误：无法解析视频 {video_path} 的帧率")
            raise ValueError(f"Invalid frame rate in {video_path}")

        format_duration_text = data.get("format", {}).get("duration")
        video_duration_text = video_stream.get("duration")
        format_duration = float(format_duration_text) if format_duration_text is not None else None
        frame_count_text = video_stream.get("nb_frames")

        frame_count = None
        if frame_count_text is not None:
            try:
                frame_count = int(frame_count_text)
            except ValueError:
                frame_count = None
        if frame_count is None:
            try:
                frame_count = count_video_frames(video_path)
            except (subprocess.CalledProcessError, ValueError):
                frame_count = None

        video_duration = float(video_duration_text) if video_duration_text is not None else None
        if video_duration is None and frame_count is not None:
            video_duration = frame_count / fps
        if video_duration is None and format_duration is not None:
            video_duration = format_duration
        if video_duration is None:
            log_callback(f"错误：无法获取视频 {video_path} 的时长")
            raise ValueError(f"Missing duration in {video_path}")

        if frame_count is None:
            frame_count = int(round(video_duration * fps))

        return {
            "width": width,
            "height": height,
            "fps": fps,
            "frame_count": max(frame_count, 1),
            "sar": video_stream.get("sample_aspect_ratio", "1:1"),
            "video_duration": video_duration,
            "format_duration": format_duration if format_duration is not None else video_duration,
            "has_audio": audio_stream is not None,
        }
    except subprocess.CalledProcessError as e:
        log_callback(f"ffprobe 失败: {e.stderr}")
        raise RuntimeError(f"ffprobe failed for {video_path}: {e.stderr}")
    except json.JSONDecodeError as e:
        log_callback(f"ffprobe 输出解析失败: {result.stdout}")
        raise RuntimeError(f"Invalid JSON output from ffprobe: {e}")

def get_video_info(video_path, log_callback):
    """获取视频的分辨率、帧率、帧数和 SAR"""
    info = probe_media_info(video_path, log_callback)
    return info["width"], info["height"], info["fps"], info["frame_count"], info["sar"]

def get_duration(video_path, log_callback):
    """获取视频流时长（秒）"""
    return probe_media_info(video_path, log_callback)["video_duration"]

def format_ffmpeg_seconds(seconds):
    return f"{seconds:.6f}"

def append_log_message(message):
    log_text.config(state=NORMAL)
    log_text.insert(END, f"[{time.strftime('%H:%M:%S')}] {message.rstrip()}\n")
    log_text.config(state=DISABLED)
    log_text.see(END)

def set_progress_value(progress_percentage):
    progress_bar['value'] = progress_percentage

def queue_error_dialog(message):
    run_on_ui_thread(messagebox.showerror, "错误", message)

def cleanup_temp_files(paths, log_callback):
    for path in paths:
        if not path:
            continue
        try:
            os.remove(path)
            log_callback(f"已删除临时文件: {path}")
        except FileNotFoundError:
            pass
        except Exception as exc:
            log_callback(f"删除临时文件失败: {exc}")

def cleanup_temp_directory(temp_dir, log_callback):
    if not temp_dir:
        return
    try:
        for entry in os.listdir(temp_dir):
            cleanup_temp_files([os.path.join(temp_dir, entry)], log_callback)
        os.rmdir(temp_dir)
        log_callback(f"已删除临时目录: {temp_dir}")
    except FileNotFoundError:
        pass
    except Exception as exc:
        log_callback(f"删除临时目录失败: {exc}")

def convert_to_60fps(video_path, output_path, log_callback, progress_callback, hwaccel_type, codec):
    """使用指定编码器将视频转换为 60 FPS，临时文件只保留视频轨"""
    log_callback(f"转换视频 {video_path} 到 60 FPS（{hwaccel_type if hwaccel_type != '禁用' else '软件编码'}, {codec}）...")

    duration = get_duration(video_path, log_callback)
    total_frames = max(int(round(duration * 60)), 1)
    log_callback(f"目标帧数 (60 FPS): {total_frames}")

    cmd = ["ffmpeg", "-y"]
    cmd.extend(get_hwaccel_args(hwaccel_type))
    cmd.extend([
        "-i", video_path,
        "-vf", "fps=60,setsar=1,setpts=PTS-STARTPTS",
        "-r", "60",
        "-start_at_zero",
    ])

    if hwaccel_type == "NVIDIA NVENC":
        cmd.extend([
            "-c:v", get_video_encoder(hwaccel_type, codec),
            "-preset", "p7",
            "-rc", "vbr",
            "-cq", "18",
            "-rc-lookahead", "60"
        ])
    elif hwaccel_type == "AMD AMF":
        cmd.extend([
            "-c:v", get_video_encoder(hwaccel_type, codec),
            "-usage", "transcoding",
            "-quality", "quality",
            "-rc", "cqp",
            "-qp_i", "16",
            "-qp_p", "16",
            "-qp_b", "18",
        ])
    elif hwaccel_type == "Intel QSV":
        cmd.extend([
            "-c:v", get_video_encoder(hwaccel_type, codec),
            "-rc", "vbr",
            "-global_quality", "18",
        ])
    else:  # 软件编码
        cmd.extend(["-c:v", get_video_encoder(hwaccel_type, codec), "-preset", "slow", "-crf", "18"])

    cmd.extend(["-an", "-pix_fmt", "yuv420p", output_path])

    timeout_duration = 300 # 5分钟超时

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=WINDOWS_NO_WINDOW
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

def merge_videos(video_a_path, video_b_path, output_path, progress_callback, log_callback, temp_dir, hwaccel_type, codec, bitrate_kbps):
    """使用指定编码器合并两个 60 FPS 视频为 120 FPS，帧交替 A₁B₁A₂B₂"""
    log_callback(f"开始合并视频为 120 帧（{hwaccel_type if hwaccel_type != '禁用' else '软件编码'}, {codec}）...")

    log_callback(f"检查视频 A: {video_a_path}")
    info_a = probe_media_info(video_a_path, log_callback)
    w1, h1, fps_a = info_a["width"], info_a["height"], info_a["fps"]
    log_callback(f"视频 A: {w1}x{h1}@{fps_a:.2f}FPS, 视频时长: {info_a['video_duration']:.2f}s")

    log_callback(f"检查视频 B: {video_b_path}")
    info_b = probe_media_info(video_b_path, log_callback)
    w2, h2, fps_b = info_b["width"], info_b["height"], info_b["fps"]
    log_callback(f"视频 B: {w2}x{h2}@{fps_b:.2f}FPS, 视频时长: {info_b['video_duration']:.2f}s")

    if (w1, h1) != (w2, h2):
        log_callback(f"错误：视频分辨率不一致！视频 A: {w1}x{h1}, 视频 B: {w2}x{h2}")
        raise ValueError("Resolution mismatch")

    temp_a_path = video_a_path
    temp_b_path = video_b_path
    created_temp_files = []

    try:
        if abs(fps_a - 60.0) > 0.1:
            temp_a_path = os.path.join(temp_dir, "video_a_60fps.mp4")
            created_temp_files.append(temp_a_path)
            log_callback("转换视频 A 到 60FPS...")
            convert_to_60fps(video_a_path, temp_a_path, log_callback, progress_callback, hwaccel_type, codec)
        if abs(fps_b - 60.0) > 0.1:
            temp_b_path = os.path.join(temp_dir, "video_b_60fps.mp4")
            created_temp_files.append(temp_b_path)
            log_callback("转换视频 B 到 60FPS...")
            convert_to_60fps(video_b_path, temp_b_path, log_callback, progress_callback, hwaccel_type, codec)

        temp_info_a = probe_media_info(temp_a_path, log_callback)
        temp_info_b = probe_media_info(temp_b_path, log_callback)
        target_frame_pairs = min(temp_info_a["frame_count"], temp_info_b["frame_count"])
        if target_frame_pairs <= 0:
            raise ValueError("无法确定有效的视频帧数")

        target_duration = target_frame_pairs / 60.0
        total_frames_120fps = target_frame_pairs * 2
        log_callback(f"按最短视频流截断：每路保留 {target_frame_pairs} 帧（60FPS），输出总帧数 {total_frames_120fps}（120FPS）")

        ffmpeg_cmd = ["ffmpeg", "-y"]
        ffmpeg_cmd.extend(get_hwaccel_args(hwaccel_type))
        ffmpeg_cmd.extend([
            "-i", temp_a_path,
            "-i", temp_b_path,
            "-i", video_a_path,
        ])

        filter_parts = [
            f"[0:v]trim=end_frame={target_frame_pairs},setsar=1,setpts=PTS-STARTPTS[v0]",
            f"[1:v]trim=end_frame={target_frame_pairs},setsar=1,setpts=PTS-STARTPTS[v1]",
            "[v0][v1]interleave[v]",
        ]
        has_audio = info_a["has_audio"]
        if has_audio:
            filter_parts.append(f"[2:a]atrim=duration={format_ffmpeg_seconds(target_duration)},asetpts=PTS-STARTPTS[a]")

        ffmpeg_cmd.extend([
            "-filter_complex", ";".join(filter_parts),
            "-map", "[v]",
        ])
        if has_audio:
            ffmpeg_cmd.extend(["-map", "[a]"])

        ffmpeg_cmd.extend([
            "-r", "120",
            "-start_at_zero",
        ])

        if hwaccel_type == "NVIDIA NVENC":
            ffmpeg_cmd.extend([
                "-c:v", get_video_encoder(hwaccel_type, codec),
                "-preset", "p7"
            ])
            if bitrate_kbps is not None:
                ffmpeg_cmd.extend(["-rc", "vbr", "-b:v", f"{bitrate_kbps}k"])
            else:
                ffmpeg_cmd.extend(["-rc", "vbr", "-cq", "18", "-rc-lookahead", "60"])
        elif hwaccel_type == "AMD AMF":
            ffmpeg_cmd.extend([
                "-c:v", get_video_encoder(hwaccel_type, codec),
                "-usage", "transcoding",
                "-quality", "quality",
            ])
            if bitrate_kbps is not None:
                ffmpeg_cmd.extend(["-b:v", f"{bitrate_kbps}k"])
            else:
                ffmpeg_cmd.extend(["-rc", "cqp", "-qp_i", "16", "-qp_p", "16", "-qp_b", "18", "-refs", "4"])
        elif hwaccel_type == "Intel QSV":
            ffmpeg_cmd.extend([
                "-c:v", get_video_encoder(hwaccel_type, codec),
                "-preset", "fast",
            ])
            if bitrate_kbps is not None:
                ffmpeg_cmd.extend(["-rc", "vbr", "-b:v", f"{bitrate_kbps}k"])
            else:
                ffmpeg_cmd.extend(["-rc", "vbr", "-global_quality", "18"])
        else:
            ffmpeg_cmd.extend(["-c:v", get_video_encoder(hwaccel_type, codec), "-preset", "slow"])
            if bitrate_kbps is not None:
                ffmpeg_cmd.extend(["-b:v", f"{bitrate_kbps}k"])
            else:
                ffmpeg_cmd.extend(["-crf", "18"])

        ffmpeg_cmd.extend(["-pix_fmt", "yuv420p"])
        if has_audio:
            ffmpeg_cmd.extend(["-c:a", "aac"])
        ffmpeg_cmd.append(output_path)

        timeout_duration = 300 # 5分钟超时

        process = subprocess.Popen(
            ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, encoding='utf-8', errors='replace', bufsize=1,
            creationflags=WINDOWS_NO_WINDOW
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
            stdout_queue.get()

        if process.returncode != 0:
            log_callback(f"处理失败，错误码: {process.returncode}")
            raise subprocess.CalledProcessError(process.returncode, ffmpeg_cmd)

        log_callback(f"视频合并完成: {output_path}")
    finally:
        cleanup_temp_files(created_temp_files, log_callback)

def start_processing():
    """开始处理视频合成"""
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

    bitrate_kbps = None
    if bitrate_enabled_var.get() == 1:
        try:
            bitrate_mbps = float(bitrate_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "请输入有效的码率（数字，单位 Mbps）")
            return
        if bitrate_mbps <= 0:
            messagebox.showerror("错误", "码率必须大于 0 Mbps")
            return
        bitrate_kbps = int(round(bitrate_mbps * 1000))

    if not check_ffmpeg(hwaccel_type, codec):
        return

    task_temp_dir = tempfile.mkdtemp(prefix="videohybridizer_")
    log_message("——————任务开始——————\n")
    log_message(f"视频 A: {video_a_path}")
    log_message(f"视频 B: {video_b_path}")
    log_message(f"输出文件: {output_file}")
    log_message(f"编码设置: {hwaccel_type if hwaccel_type != '禁用' else '软件编码'}, {codec}")
    if bitrate_kbps is not None:
        log_message(f"目标码率: {bitrate_kbps} kbps")
    progress_bar['value'] = 0
    threading.Thread(
        target=merge_and_compress,
        args=(video_a_path, video_b_path, output_file, task_temp_dir, hwaccel_type, codec, bitrate_kbps),
        daemon=True
    ).start()

def merge_and_compress(video_a_path, video_b_path, final_output_path, temp_dir, hwaccel_type, codec, bitrate_kbps):
    """合成视频"""
    def progress_callback(current_frame, total_frames, stage):
        progress_percentage = min((current_frame / total_frames) * 100, 100)
        run_on_ui_thread(set_progress_value, progress_percentage)

    try:
        merge_videos(video_a_path, video_b_path, final_output_path, progress_callback, log_message, temp_dir, hwaccel_type, codec, bitrate_kbps)
        log_message(f"视频处理完成！输出文件: {final_output_path}\n")
    except Exception as e:
        log_message(f"处理失败: {str(e)}\n")
        queue_error_dialog(f"视频处理失败: {str(e)}")
    finally:
        cleanup_temp_directory(temp_dir, log_message)

def log_message(message):
    """记录日志信息"""
    run_on_ui_thread(append_log_message, message)

root = Tk()
root.title("视频杂交器 —— VideoHybridizer")

# 设置窗口图标
set_window_icon(root)

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

usage_title_font = tkfont.nametofont("TkDefaultFont").copy()
usage_title_font.configure(family="Microsoft YaHei UI", size=14, weight="bold")
usage_heading_font = tkfont.nametofont("TkDefaultFont").copy()
usage_heading_font.configure(family="Microsoft YaHei UI", size=11, weight="bold")
usage_body_font = tkfont.nametofont("TkDefaultFont").copy()
usage_body_font.configure(family="Microsoft YaHei UI", size=10)
usage_code_font = usage_body_font.copy()
usage_code_font.configure(size=11)

style.configure("UsageTitle.TLabel", font=usage_title_font, padding=(0, 0, 0, 4))
style.configure("UsageSubtitle.TLabel", font=usage_body_font, foreground="#5A6472", padding=(0, 0, 0, 10))
style.configure("UsageCard.TLabelframe", padding=12)
style.configure("UsageCard.TLabelframe.Label", font=usage_heading_font)
style.configure("UsageBody.TLabel", font=usage_body_font, padding=(0, 2, 0, 2))
style.configure("UsageCode.TLabel", font=usage_code_font, padding=(0, 2, 0, 2))
style.configure("UsageKey.TLabel", font=usage_heading_font, padding=(0, 2, 12, 2))

main_frame = Frame(root)
main_frame.pack(padx=20, pady=20, expand=True, fill='both')

inner_frame = Frame(main_frame)
inner_frame.pack(expand=True, fill='both')
inner_frame.columnconfigure(1, weight=1)
inner_frame.rowconfigure(6, weight=1)

def toggle_bitrate_entry():
    if bitrate_enabled_var.get() == 1:
        bitrate_entry.config(state=NORMAL)
    else:
        bitrate_entry.config(state=DISABLED)

def show_usage_dialog():
    global usage_window

    if usage_window is not None and usage_window.winfo_exists():
        usage_window.deiconify()
        usage_window.lift()
        usage_window.focus_force()
        return

    dialog = Toplevel(root)
    dialog.withdraw()
    dialog.title("使用说明")
    dialog.transient(root)
    set_window_icon(dialog)

    dialog_frame = Frame(dialog)
    dialog_frame.pack(expand=True, fill='both', padx=16, pady=16)
    dialog_frame.rowconfigure(1, weight=1)
    dialog_frame.columnconfigure(0, weight=1)

    header_frame = Frame(dialog_frame)
    header_frame.grid(row=0, column=0, columnspan=2, sticky="ew")
    header_frame.columnconfigure(0, weight=1)

    Label(header_frame, text="使用说明", style="UsageTitle.TLabel").grid(row=0, column=0, sticky="w")
    Label(
        header_frame,
        text=USAGE_SUMMARY,
        style="UsageSubtitle.TLabel",
        justify="left",
        wraplength=700,
    ).grid(row=1, column=0, sticky="w")

    content_frame = Frame(dialog_frame)
    content_frame.grid(row=1, column=0, sticky="nsew")
    content_frame.rowconfigure(0, weight=1)
    content_frame.columnconfigure(0, weight=1)

    usage_canvas = Canvas(content_frame, highlightthickness=0, borderwidth=0)
    usage_canvas.grid(row=0, column=0, sticky="nsew")

    usage_scrollbar = Scrollbar(content_frame, command=usage_canvas.yview)
    usage_scrollbar.grid(row=0, column=1, sticky='ns')
    usage_canvas.configure(yscrollcommand=usage_scrollbar.set)

    usage_body = Frame(usage_canvas)
    usage_window_id = usage_canvas.create_window((0, 0), window=usage_body, anchor="nw")
    usage_body.columnconfigure(0, weight=1)

    def sync_scroll_region(_event):
        usage_canvas.configure(scrollregion=usage_canvas.bbox("all"))

    def sync_content_width(event):
        usage_canvas.itemconfigure(usage_window_id, width=event.width)

    usage_body.bind("<Configure>", sync_scroll_region)
    usage_canvas.bind("<Configure>", sync_content_width)

    timeline_card = LabelFrame(usage_body, text="帧排列图解", style="UsageCard.TLabelframe")
    timeline_card.grid(row=0, column=0, sticky="ew", pady=(0, 12))
    timeline_card.columnconfigure(1, weight=1)

    for row_index, (label_text, sequence_text) in enumerate(USAGE_TIMELINE):
        Label(timeline_card, text=label_text, style="UsageKey.TLabel").grid(row=row_index, column=0, sticky="nw")
        Label(timeline_card, text=sequence_text, style="UsageCode.TLabel").grid(row=row_index, column=1, sticky="nw")

    example_card = LabelFrame(usage_body, text="实测", style="UsageCard.TLabelframe")
    example_card.grid(row=1, column=0, sticky="ew", pady=(0, 12))
    example_card.columnconfigure(0, weight=1)

    for row_index, example_text in enumerate(USAGE_EXAMPLES):
        Label(
            example_card,
            text=example_text,
            style="UsageBody.TLabel",
            justify="left",
            wraplength=680,
        ).grid(row=row_index, column=0, sticky="w")

    notes_card = LabelFrame(usage_body, text="注意事项", style="UsageCard.TLabelframe")
    notes_card.grid(row=2, column=0, sticky="ew")
    notes_card.columnconfigure(0, weight=1)

    for row_index, note_text in enumerate(USAGE_NOTES, start=1):
        Label(
            notes_card,
            text=f"{row_index}. {note_text}",
            style="UsageBody.TLabel",
            justify="left",
            wraplength=680,
        ).grid(row=row_index - 1, column=0, sticky="w")

    def handle_close():
        global usage_window
        usage_window = None
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", handle_close)
    usage_window = dialog

    dialog.update_idletasks()

    desired_width = max(header_frame.winfo_reqwidth(), usage_body.winfo_reqwidth()) + 48
    desired_height = header_frame.winfo_reqheight() + usage_body.winfo_reqheight() + 48
    scrollbar_width = usage_scrollbar.winfo_reqwidth() + 8

    max_width = max(760, int(dialog.winfo_screenwidth() * 0.88))
    max_height = max(460, int(dialog.winfo_screenheight() * 0.88))
    needs_vertical_scroll = desired_height > max_height

    if needs_vertical_scroll:
        usage_scrollbar.grid()
        final_width = min(max(desired_width + scrollbar_width, 780), max_width)
        final_height = max_height
        canvas_height = max(final_height - header_frame.winfo_reqheight() - 64, 260)
        usage_canvas.configure(height=canvas_height)
    else:
        usage_scrollbar.grid_remove()
        final_width = min(max(desired_width, 760), max_width)
        final_height = min(max(desired_height, 420), max_height)
        usage_canvas.configure(height=usage_body.winfo_reqheight())

    dialog.update_idletasks()
    final_width = min(max(final_width, dialog.winfo_reqwidth()), max_width)
    final_height = min(max(final_height, dialog.winfo_reqheight()), max_height)

    root.update_idletasks()
    pos_x = root.winfo_rootx() + max((root.winfo_width() - final_width) // 2, 20)
    pos_y = root.winfo_rooty() + max((root.winfo_height() - final_height) // 2, 20)
    pos_x = max(20, min(pos_x, dialog.winfo_screenwidth() - final_width - 20))
    pos_y = max(20, min(pos_y, dialog.winfo_screenheight() - final_height - 40))

    dialog.minsize(760, 420)
    dialog.geometry(f"{final_width}x{final_height}+{pos_x}+{pos_y}")
    dialog.deiconify()
    dialog.lift()
    dialog.focus_force()

video_a_var = StringVar()
video_b_var = StringVar()
output_file_var = StringVar()
hwaccel_var = StringVar(value="NVIDIA NVENC")
codec_var = StringVar(value="H.264")
bitrate_enabled_var = IntVar(value=0)
bitrate_var = StringVar(value="5")

Label(inner_frame, text="视频 A").grid(row=0, column=0, padx=10, pady=5)
Entry(inner_frame, textvariable=video_a_var, width=50).grid(row=0, column=1, padx=10, pady=5, sticky="ew")
Button(inner_frame, text="选择", command=lambda: select_video(video_a_var)).grid(row=0, column=2, padx=10, pady=5)

Label(inner_frame, text="视频 B").grid(row=1, column=0, padx=10, pady=5)
Entry(inner_frame, textvariable=video_b_var, width=50).grid(row=1, column=1, padx=10, pady=5, sticky="ew")
Button(inner_frame, text="选择", command=lambda: select_video(video_b_var)).grid(row=1, column=2, padx=10, pady=5)

Label(inner_frame, text="导出位置").grid(row=2, column=0, padx=10, pady=5)
Entry(inner_frame, textvariable=output_file_var, width=50).grid(row=2, column=1, padx=10, pady=5, sticky="ew")
Button(inner_frame, text="选择", command=lambda: select_output_file(output_file_var)).grid(row=2, column=2, padx=10, pady=5)

Label(inner_frame, text="输出设置").grid(row=3, column=0, padx=10, pady=5, sticky="ne")
settings_frame = Frame(inner_frame)
settings_frame.grid(row=3, column=1, columnspan=2, padx=10, pady=5, sticky="ew")

Label(settings_frame, text="硬件加速").grid(row=0, column=0, padx=(0, 6), pady=(0, 4), sticky="w")
hwaccel_combo = Combobox(settings_frame, textvariable=hwaccel_var, values=["NVIDIA NVENC", "AMD AMF", "Intel QSV", "禁用"], state="readonly", width=15)
hwaccel_combo.grid(row=0, column=1, padx=(0, 12), pady=(0, 4), sticky="w")

Label(settings_frame, text="编码格式").grid(row=0, column=2, padx=(0, 6), pady=(0, 4), sticky="w")
codec_combo = Combobox(settings_frame, textvariable=codec_var, values=["H.264", "H.265"], state="readonly", width=9)
codec_combo.grid(row=0, column=3, padx=(0, 10), pady=(0, 4), sticky="w")

bitrate_frame = Frame(settings_frame)
bitrate_frame.grid(row=0, column=4, padx=(0, 6), pady=(0, 4), sticky="w")

Checkbutton(bitrate_frame, text="码率", variable=bitrate_enabled_var, command=toggle_bitrate_entry).grid(row=0, column=0, padx=(0, 6), pady=0, sticky="w")

bitrate_entry = Entry(bitrate_frame, textvariable=bitrate_var, width=5)
bitrate_entry.grid(row=0, column=1, padx=(0, 6), pady=0, sticky="w")
bitrate_entry.config(state=DISABLED if bitrate_enabled_var.get() == 0 else NORMAL)

Label(bitrate_frame, text="Mbps").grid(row=0, column=2, padx=(0, 6), pady=0, sticky="w")

USAGE_SUMMARY = "两个视频会先统一到 60 FPS，再交错输出为 120 FPS，并按最短视频流截断；最终只保留并裁剪视频 A 的音轨。"

USAGE_TIMELINE = [
    ("视频 A (60 FPS)", "A₁ - A₂ - A₃ - A₄ - A₅ - ..."),
    ("视频 B (60 FPS)", "B₁ - B₂ - B₃ - B₄ - B₅ - ..."),
    ("合成视频 (120 FPS)", "A₁ - B₁ - A₂ - B₂ - A₃ - B₃ - A₄ - B₄ - ..."),
]

USAGE_EXAMPLES = [
    "视频 A：哔哩哔哩大会员 60FPS 的画面，60FPS 保留每四帧中的第 1、3 帧。",
    "视频 B：哔哩哔哩 30FPS 的画面，30FPS 保留每四帧中的第 2 帧。",
]

USAGE_NOTES = [
    "待处理的两个视频必须保证分辨率完全相同。",
    "若两个视频长度不同，合成结果会按最短视频流截断。",
    "点击“开始合成”后，程序会先检查帧率并转换到 60 帧，再合成 120 帧输出。",
    "转换阶段的临时文件不保留音轨，最终输出只保留并裁剪视频 A 的音轨。",
    "硬件加速是可选项，但当前所选编码器必须被 FFmpeg 支持。",
]
action_frame = Frame(inner_frame)
action_frame.grid(row=4, column=0, columnspan=3, pady=(10, 14))

Button(action_frame, text="开始合成", command=start_processing).grid(row=0, column=0, padx=(0, 10))
Button(action_frame, text="使用说明", command=show_usage_dialog).grid(row=0, column=1)

progress_bar = Progressbar(inner_frame, orient='horizontal', length=500, mode='determinate')
progress_bar.grid(row=5, column=0, columnspan=3, pady=8, sticky="ew", padx=10)

log_text = Text(inner_frame, wrap='word', height=8, state=DISABLED)
log_text.grid(row=6, column=0, columnspan=3, padx=10, pady=(8, 10), sticky="nsew")
scrollbar = Scrollbar(inner_frame, command=log_text.yview)
scrollbar.grid(row=6, column=3, sticky='nsew')
log_text['yscrollcommand'] = scrollbar.set

root.after(50, process_ui_queue)
root.geometry("840x620")
root.update_idletasks()
root.minsize(740, 500)
root.mainloop()

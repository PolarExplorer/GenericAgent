#!/usr/bin/env python3
"""
音频/视频转文字工具 - 使用OpenAI Whisper API
支持音频文件直接转录，视频文件自动提取音频后转录。

用法:
    python audio_to_text.py <input_file> [output.txt]
    python audio_to_text.py interview.mp3
    python audio_to_text.py lecture.mp4 transcript.txt

支持格式:
    音频: mp3, wav, m4a, flac, ogg, webm
    视频: mp4, avi, mkv, mov, webm (自动提取音频)

环境变量:
    OPENAI_API_KEY: OpenAI API密钥 (必需)
    OPENAI_API_BASE: API基础URL (可选，默认https://api.openai.com/v1)
"""

import sys
import os
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

# 默认配置
DEFAULT_MODEL = "whisper-1"
DEFAULT_LANGUAGE = None  # 自动检测
DEFAULT_RESPONSE_FORMAT = "verbose_json"

# 支持的格式
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.flac', '.ogg', '.webm', '.mpga', '.mpeg'}
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.webm', '.flv', '.wmv'}


def check_ffmpeg():
    """检查ffmpeg是否安装"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def extract_audio_from_video(video_path: str, output_path: str) -> bool:
    """从视频文件提取音频"""
    try:
        # 使用ffmpeg提取音频为mp3格式
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-vn",  # 不包含视频
            "-acodec", "libmp3lame",  # MP3编码
            "-q:a", "2",  # 高质量
            "-y",  # 覆盖输出文件
            output_path
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10分钟超时
        )
        
        if result.returncode != 0:
            print(f"❌ ffmpeg提取音频失败: {result.stderr}")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 提取音频时出错: {e}")
        return False


def transcribe_audio(audio_path: str, api_key: str, api_base: str = None, 
                    model: str = DEFAULT_MODEL, language: str = DEFAULT_LANGUAGE) -> dict:
    """使用OpenAI Whisper API转录音频"""
    try:
        import requests
        
        # 准备API URL
        base_url = api_base or "https://api.openai.com/v1"
        url = f"{base_url}/audio/transcriptions"
        
        # 准备请求头
        headers = {
            "Authorization": f"Bearer {api_key}"
        }
        
        # 准备文件和数据
        with open(audio_path, "rb") as audio_file:
            files = {
                "file": (os.path.basename(audio_path), audio_file, "audio/mpeg")
            }
            
            data = {
                "model": model,
                "response_format": DEFAULT_RESPONSE_FORMAT,
                "timestamp_granularities[]": "segment"
            }
            
            if language:
                data["language"] = language
            
            # 发送请求
            response = requests.post(url, headers=headers, files=files, data=data, timeout=300)
        
        if response.status_code != 200:
            print(f"❌ API请求失败: {response.status_code}")
            print(f"   错误信息: {response.text}")
            return None
        
        return response.json()
        
    except ImportError:
        print("❌ 需要安装requests库: pip install requests")
        return None
    except Exception as e:
        print(f"❌ 转录时出错: {e}")
        return None


def format_transcript(result: dict, include_timestamps: bool = True) -> str:
    """格式化转录结果为可读文本"""
    if not result:
        return ""
    
    lines = []
    
    # 获取完整文本
    text = result.get("text", "")
    language = result.get("language", "unknown")
    duration = result.get("duration", 0)
    
    # 添加元信息
    lines.append(f"# 转录结果")
    lines.append(f"# 语言: {language}")
    lines.append(f"# 时长: {duration:.1f}秒 ({duration/60:.1f}分钟)")
    lines.append(f"# 转录时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    
    # 如果有分段信息，添加带时间戳的文本
    segments = result.get("segments", [])
    if segments and include_timestamps:
        lines.append("## 带时间戳版本")
        lines.append("")
        
        for segment in segments:
            start = segment.get("start", 0)
            end = segment.get("end", 0)
            segment_text = segment.get("text", "").strip()
            
            if segment_text:
                # 格式化时间戳
                start_min, start_sec = divmod(start, 60)
                end_min, end_sec = divmod(end, 60)
                timestamp = f"[{int(start_min):02d}:{start_sec:05.2f} - {int(end_min):02d}:{end_sec:05.2f}]"
                
                lines.append(f"{timestamp} {segment_text}")
        
        lines.append("")
        lines.append("## 完整文本")
        lines.append("")
    
    # 添加完整文本
    lines.append(text)
    
    return "\n".join(lines)


def get_file_extension(filepath: str) -> str:
    """获取文件扩展名（小写）"""
    return Path(filepath).suffix.lower()


def is_audio_file(filepath: str) -> bool:
    """检查是否为音频文件"""
    return get_file_extension(filepath) in AUDIO_EXTENSIONS


def is_video_file(filepath: str) -> bool:
    """检查是否为视频文件"""
    return get_file_extension(filepath) in VIDEO_EXTENSIONS


def main():
    """主函数"""
    # 检查参数
    if len(sys.argv) < 2:
        print("用法: python audio_to_text.py <input_file> [output.txt]")
        print("")
        print("示例:")
        print("  python audio_to_text.py interview.mp3")
        print("  python audio_to_text.py lecture.mp4 transcript.txt")
        print("")
        print("支持格式:")
        print(f"  音频: {', '.join(sorted(AUDIO_EXTENSIONS))}")
        print(f"  视频: {', '.join(sorted(VIDEO_EXTENSIONS))}")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    # 检查输入文件
    if not os.path.exists(input_path):
        print(f"❌ 文件不存在: {input_path}")
        sys.exit(1)
    
    # 获取API密钥
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("❌ 未设置OPENAI_API_KEY环境变量")
        print("   请设置: export OPENAI_API_KEY=sk-...")
        sys.exit(1)
    
    api_base = os.environ.get("OPENAI_API_BASE")
    
    # 检查文件类型
    file_ext = get_file_extension(input_path)
    is_audio = is_audio_file(input_path)
    is_video = is_video_file(input_path)
    
    if not is_audio and not is_video:
        print(f"❌ 不支持的文件格式: {file_ext}")
        print(f"   支持的音频格式: {', '.join(sorted(AUDIO_EXTENSIONS))}")
        print(f"   支持的视频格式: {', '.join(sorted(VIDEO_EXTENSIONS))}")
        sys.exit(1)
    
    # 如果是视频文件，需要提取音频
    audio_path = input_path
    temp_audio = None
    
    if is_video:
        print(f"📹 检测到视频文件，正在提取音频...")
        
        # 检查ffmpeg
        if not check_ffmpeg():
            print("❌ 需要安装ffmpeg才能处理视频文件")
            print("   请访问: https://ffmpeg.org/download.html")
            sys.exit(1)
        
        # 创建临时音频文件
        temp_audio = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        temp_audio.close()
        
        # 提取音频
        if not extract_audio_from_video(input_path, temp_audio.name):
            print("❌ 从视频提取音频失败")
            os.unlink(temp_audio.name)
            sys.exit(1)
        
        audio_path = temp_audio.name
        print(f"✅ 音频提取完成")
    
    # 转录音频
    print(f"🎙️  正在转录音频...")
    print(f"   文件: {os.path.basename(input_path)}")
    print(f"   大小: {os.path.getsize(input_path) / 1024 / 1024:.1f} MB")
    
    result = transcribe_audio(
        audio_path=audio_path,
        api_key=api_key,
        api_base=api_base
    )
    
    # 清理临时文件
    if temp_audio and os.path.exists(temp_audio.name):
        os.unlink(temp_audio.name)
    
    if not result:
        print("❌ 转录失败")
        sys.exit(1)
    
    # 格式化结果
    transcript = format_transcript(result)
    
    # 确定输出路径
    if not output_path:
        input_stem = Path(input_path).stem
        output_path = f"{input_stem}_transcript.txt"
    
    # 保存结果
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(transcript)
    
    # 输出统计
    duration = result.get("duration", 0)
    text_length = len(result.get("text", ""))
    
    print(f"\n✅ 转录完成!")
    print(f"   输出文件: {output_path}")
    print(f"   时长: {duration:.1f}秒 ({duration/60:.1f}分钟)")
    print(f"   文本长度: {text_length}字符")
    print(f"   语言: {result.get('language', 'unknown')}")
    
    # 显示预览
    print(f"\n📄 文本预览:")
    preview = result.get("text", "")[:200]
    if len(result.get("text", "")) > 200:
        preview += "..."
    print(f"   {preview}")


if __name__ == "__main__":
    main()

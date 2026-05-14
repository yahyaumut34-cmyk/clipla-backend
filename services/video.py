from moviepy import VideoFileClip
import os
from typing import Tuple

class VideoProcessor:
    """Video processing utilities"""
    
    async def extract_audio(self, video_path: str) -> Tuple[str, float]:
        """Extract audio from video and return audio path and duration"""
        video = VideoFileClip(video_path)
        duration = video.duration
        
        # Extract audio
        audio_path = video_path.replace('.mp4', '_audio.wav').replace('.mov', '_audio.wav')
        video.audio.write_audiofile(audio_path, logger=None)
        video.close()
        
        return audio_path, duration
    
    async def get_video_info(self, video_path: str) -> dict:
        """Get video metadata"""
        video = VideoFileClip(video_path)
        info = {
            "duration": video.duration,
            "fps": video.fps,
            "size": video.size,
            "has_audio": video.audio is not None
        }
        video.close()
        return info
    
    def cleanup_temp_files(self, *paths: str):
        """Remove temporary files"""
        for path in paths:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

video_processor = VideoProcessor()

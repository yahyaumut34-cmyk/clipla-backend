import librosa
import numpy as np
from typing import Dict, List, Any

class AudioAnalyzer:
    """Basic audio analysis utilities"""
    
    def __init__(self):
        self.silence_threshold = 0.01
        self.low_energy_threshold = 0.02
    
    async def analyze_audio(self, audio_path: str, duration: float) -> Dict[str, Any]:
        """Analyze audio for silence, energy, speech segments"""
        y, sr = librosa.load(audio_path, sr=None)
        
        # Calculate RMS energy
        hop_length = 512
        frame_length = 2048
        rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
        times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop_length)
        
        # Detect segments
        segments = self._detect_segments(rms, times, duration)
        statistics = self._calculate_statistics(segments, duration)
        
        return {
            "duration": duration,
            "sample_rate": sr,
            "segments": segments,
            "statistics": statistics
        }
    
    def _detect_segments(self, rms: np.ndarray, times: np.ndarray, duration: float) -> List[Dict]:
        """Detect and classify audio segments"""
        segments = []
        current_type = self._classify_frame(rms[0])
        start_time = 0.0
        
        for i in range(1, len(rms)):
            frame_type = self._classify_frame(rms[i])
            
            if frame_type != current_type or i == len(rms) - 1:
                end_time = times[i] if i < len(times) else duration
                segments.append({
                    "start": round(start_time, 2),
                    "end": round(end_time, 2),
                    "type": current_type,
                    "energy_level": float(np.mean(rms[int(start_time * len(rms) / duration):i]))
                })
                start_time = end_time
                current_type = frame_type
        
        return segments
    
    def _classify_frame(self, rms_value: float) -> str:
        """Classify frame as silence, low-energy, or high-energy"""
        if rms_value < self.silence_threshold:
            return "silence"
        elif rms_value < self.low_energy_threshold:
            return "low-energy"
        else:
            return "high-energy"
    
    def _calculate_statistics(self, segments: List[Dict], duration: float) -> Dict:
        """Calculate statistics from segments"""
        silence_duration = sum(s['end'] - s['start'] for s in segments if s['type'] == 'silence')
        low_energy_duration = sum(s['end'] - s['start'] for s in segments if s['type'] == 'low-energy')
        high_energy_duration = sum(s['end'] - s['start'] for s in segments if s['type'] == 'high-energy')
        
        return {
            "total_segments": len(segments),
            "silence_percentage": round((silence_duration / duration) * 100, 2) if duration > 0 else 0,
            "low_energy_percentage": round((low_energy_duration / duration) * 100, 2) if duration > 0 else 0,
            "high_energy_percentage": round((high_energy_duration / duration) * 100, 2) if duration > 0 else 0
        }

audio_analyzer = AudioAnalyzer()

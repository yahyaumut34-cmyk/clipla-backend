from schemas import EditPlan, EditAction, FFmpegCommand, CompileResult
from typing import List, Tuple
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

class TimelineCompiler:
    """
    Compile edit plans into ffmpeg commands (no execution).
    Generates deterministic command strings for video editing pipeline.
    """
    
    def __init__(self):
        self.temp_dir = "/tmp/video_edits"
        
    async def compile_edit_plan(
        self, 
        edit_plan: EditPlan, 
        video_path: str,
        original_duration: float
    ) -> CompileResult:
        """
        Compile edit plan into ffmpeg commands without execution.
        
        Args:
            edit_plan: Validated edit plan from StrategyAgent
            video_path: Path to source video file
            original_duration: Original video duration in seconds
            
        Returns:
            CompileResult with generated ffmpeg commands
        """
        if not isinstance(edit_plan, EditPlan):
            raise ValueError("edit_plan must be EditPlan instance")
        
        if not os.path.exists(video_path):
            raise ValueError(f"Video file not found: {video_path}")
        
        if original_duration <= 0:
            raise ValueError(f"Invalid duration: {original_duration}")
        
        logger.info(f"TimelineCompiler: Compiling {len(edit_plan.edits)} edits")
        
        # Group edits by type
        cut_edits = [e for e in edit_plan.edits if e.action == "cut"]
        speed_edits = [e for e in edit_plan.edits if e.action == "speed"]
        audio_edits = [e for e in edit_plan.edits if e.action == "audio"]
        
        commands: List[FFmpegCommand] = []
        warnings: List[str] = []
        
        # Generate commands for each edit type
        if cut_edits:
            cut_commands, cut_warnings = self._generate_cut_pipeline(
                cut_edits, video_path, original_duration
            )
            commands.extend(cut_commands)
            warnings.extend(cut_warnings)
        
        if speed_edits:
            speed_commands, speed_warnings = self._generate_speed_commands(
                speed_edits, video_path
            )
            commands.extend(speed_commands)
            warnings.extend(speed_warnings)
        
        if audio_edits:
            audio_commands, audio_warnings = self._generate_audio_commands(
                audio_edits, video_path
            )
            commands.extend(audio_commands)
            warnings.extend(audio_warnings)
        
        # If we have cuts, generate merge command
        if len(cut_edits) > 0:
            merge_cmd, merge_warnings = self._generate_merge_command(
                cut_edits, video_path, original_duration
            )
            if merge_cmd:
                commands.append(merge_cmd)
            warnings.extend(merge_warnings)
        
        # Calculate estimated steps
        estimated_steps = len(commands)
        if cut_edits and len(cut_edits) > 1:
            estimated_steps += 1  # Concat step
        
        result = CompileResult(
            status="compiled",
            input_edit_plan=edit_plan,
            generated_ffmpeg_commands=commands,
            total_commands=len(commands),
            estimated_steps=estimated_steps,
            warnings=warnings,
            metadata={
                "cuts": len(cut_edits),
                "speed_changes": len(speed_edits),
                "audio_adjustments": len(audio_edits),
                "requires_merge": len(cut_edits) > 0
            }
        )
        
        logger.info(f"TimelineCompiler: Generated {len(commands)} commands")
        return result
    
    def _generate_cut_pipeline(
        self, 
        cut_edits: List[EditAction], 
        video_path: str,
        duration: float
    ) -> Tuple[List[FFmpegCommand], List[str]]:
        """Generate cut commands for removing segments"""
        commands = []
        warnings = []
        
        # Sort cuts by start time
        sorted_cuts = sorted(cut_edits, key=lambda e: e.start_time)
        
        # Build keep segments (inverse of cuts)
        keep_segments = []
        last_end = 0.0
        
        for cut in sorted_cuts:
            if cut.start_time > last_end:
                keep_segments.append((last_end, cut.start_time))
            last_end = cut.end_time
        
        # Add final segment if exists
        if last_end < duration:
            keep_segments.append((last_end, duration))
        
        if not keep_segments:
            warnings.append("All segments cut - no output will be generated")
            return commands, warnings
        
        # Generate trim commands for each keep segment
        video_name = Path(video_path).stem
        
        for i, (start, end) in enumerate(keep_segments):
            segment_duration = end - start
            output_file = f"{self.temp_dir}/{video_name}_segment_{i:03d}.mp4"
            
            # ffmpeg trim command
            cmd = (
                f"ffmpeg -i {video_path} "
                f"-ss {start:.3f} -t {segment_duration:.3f} "
                f"-c copy -avoid_negative_ts 1 "
                f"{output_file}"
            )
            
            commands.append(FFmpegCommand(
                command_type="cut",
                command=cmd,
                edit_index=i,
                input_file=video_path,
                output_file=output_file,
                description=f"Extract segment {i}: {start:.2f}s-{end:.2f}s ({segment_duration:.2f}s)"
            ))
        
        logger.info(f"TimelineCompiler: Generated {len(keep_segments)} cut segments")
        return commands, warnings
    
    def _generate_merge_command(
        self,
        cut_edits: List[EditAction],
        video_path: str,
        duration: float
    ) -> Tuple[FFmpegCommand, List[str]]:
        """Generate merge command for concatenating segments"""
        warnings = []
        
        # Build keep segments
        sorted_cuts = sorted(cut_edits, key=lambda e: e.start_time)
        keep_segments = []
        last_end = 0.0
        
        for cut in sorted_cuts:
            if cut.start_time > last_end:
                keep_segments.append((last_end, cut.start_time))
            last_end = cut.end_time
        
        if last_end < duration:
            keep_segments.append((last_end, duration))
        
        if len(keep_segments) <= 1:
            warnings.append("Only one segment - merge not needed")
            return None, warnings
        
        video_name = Path(video_path).stem
        concat_list = f"{self.temp_dir}/{video_name}_concat_list.txt"
        output_file = f"{self.temp_dir}/{video_name}_merged.mp4"
        
        # Build concat file content
        concat_content = "\n".join([
            f"file '{video_name}_segment_{i:03d}.mp4'"
            for i in range(len(keep_segments))
        ])
        
        # ffmpeg concat command
        cmd = (
            f"ffmpeg -f concat -safe 0 -i {concat_list} "
            f"-c copy {output_file}"
        )
        
        merge_cmd = FFmpegCommand(
            command_type="merge",
            command=cmd,
            edit_index=None,
            input_file=concat_list,
            output_file=output_file,
            description=f"Merge {len(keep_segments)} segments into final video"
        )
        
        warnings.append(f"Requires concat list file: {concat_list}")
        warnings.append(f"Concat list content:\n{concat_content}")
        
        return merge_cmd, warnings
    
    def _generate_speed_commands(
        self,
        speed_edits: List[EditAction],
        video_path: str
    ) -> Tuple[List[FFmpegCommand], List[str]]:
        """Generate speed change commands"""
        commands = []
        warnings = []
        
        video_name = Path(video_path).stem
        
        for i, edit in enumerate(speed_edits):
            factor = edit.parameters.get("factor", 1.0)
            
            if factor <= 0 or factor > 3:
                warnings.append(f"Speed edit {i}: Invalid factor {factor}, skipping")
                continue
            
            # Calculate setpts and atempo values
            setpts_value = 1.0 / factor
            atempo_value = factor
            
            # atempo filter has limits: 0.5 to 2.0
            if atempo_value < 0.5 or atempo_value > 2.0:
                warnings.append(
                    f"Speed edit {i}: atempo {atempo_value} out of range (0.5-2.0), "
                    f"may require chaining"
                )
            
            output_file = f"{self.temp_dir}/{video_name}_speed_{i:03d}.mp4"
            
            # ffmpeg speed command
            cmd = (
                f"ffmpeg -i {video_path} "
                f"-ss {edit.start_time:.3f} -t {edit.end_time - edit.start_time:.3f} "
                f"-filter_complex \"[0:v]setpts={setpts_value:.3f}*PTS[v];[0:a]atempo={atempo_value:.3f}[a]\" "
                f"-map \"[v]\" -map \"[a]\" "
                f"{output_file}"
            )
            
            commands.append(FFmpegCommand(
                command_type="speed",
                command=cmd,
                edit_index=i,
                input_file=video_path,
                output_file=output_file,
                description=f"Speed {factor}x: {edit.start_time:.2f}s-{edit.end_time:.2f}s"
            ))
        
        if commands:
            warnings.append("Speed changes require re-encoding (slower processing)")
        
        return commands, warnings
    
    def _generate_audio_commands(
        self,
        audio_edits: List[EditAction],
        video_path: str
    ) -> Tuple[List[FFmpegCommand], List[str]]:
        """Generate audio adjustment commands"""
        commands = []
        warnings = []
        
        video_name = Path(video_path).stem
        
        for i, edit in enumerate(audio_edits):
            normalize = edit.parameters.get("normalize", False)
            volume = edit.parameters.get("volume")
            
            output_file = f"{self.temp_dir}/{video_name}_audio_{i:03d}.mp4"
            
            filters = []
            
            if normalize:
                # Loudness normalization
                filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
            
            if volume is not None:
                if not isinstance(volume, (int, float)) or volume < 0 or volume > 2:
                    warnings.append(f"Audio edit {i}: Invalid volume {volume}, skipping")
                    continue
                # Volume adjustment
                filters.append(f"volume={volume:.2f}")
            
            if not filters:
                warnings.append(f"Audio edit {i}: No valid parameters, skipping")
                continue
            
            filter_str = ",".join(filters)
            
            # ffmpeg audio command
            cmd = (
                f"ffmpeg -i {video_path} "
                f"-ss {edit.start_time:.3f} -t {edit.end_time - edit.start_time:.3f} "
                f"-af \"{filter_str}\" "
                f"-c:v copy "
                f"{output_file}"
            )
            
            commands.append(FFmpegCommand(
                command_type="audio",
                command=cmd,
                edit_index=i,
                input_file=video_path,
                output_file=output_file,
                description=f"Audio adjust: {edit.start_time:.2f}s-{edit.end_time:.2f}s ({filter_str})"
            ))
        
        return commands, warnings
    
    def validate_command_structure(self, command: FFmpegCommand) -> List[str]:
        """
        Validate ffmpeg command structure without execution.
        QC helper method.
        """
        errors = []
        
        # Check command starts with ffmpeg
        if not command.command.strip().startswith("ffmpeg"):
            errors.append(f"Command must start with 'ffmpeg': {command.command[:50]}")
        
        # Check input file specified
        if "-i " not in command.command:
            errors.append("Command missing input file (-i)")
        
        # Check output file at end
        if not command.output_file or command.output_file not in command.command:
            errors.append("Output file not found in command")
        
        # Check command type matches content
        if command.command_type == "cut" and "-ss" not in command.command:
            errors.append("Cut command missing -ss (start time)")
        
        if command.command_type == "speed" and "setpts" not in command.command:
            errors.append("Speed command missing setpts filter")
        
        if command.command_type == "audio" and "-af" not in command.command:
            errors.append("Audio command missing -af (audio filter)")
        
        if command.command_type == "merge" and "-f concat" not in command.command:
            errors.append("Merge command missing -f concat")
        
        return errors

timeline_compiler = TimelineCompiler()

"""字幕转换引擎"""
import json
import re
from datetime import datetime
from typing import Any, Dict, List


class SubtitleConverter:
    """字幕格式转换器"""
    
    def __init__(self):
        self.srt_time_pattern = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})')
        self.ass_time_pattern = re.compile(r'(\d+):(\d{2}):(\d{2})\.(\d{2})')
    
    def seconds_to_time_str(self, seconds: float, format_type: str = 'lrc') -> str:
        if seconds < 0:
            seconds = 0
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        ms_part = int((seconds - int(seconds)) * 100)
        
        if format_type == 'lrc':
            return f"{minutes:02d}:{secs + ms_part/100:05.2f}"
        elif format_type == 'srt':
            ms_full = int((seconds - int(seconds)) * 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms_full:03d}"
        elif format_type == 'ass':
            return f"{hours}:{minutes:02d}:{secs:02d}.{ms_part:02d}"
        elif format_type == 'timed_text':
            return f"{minutes:02d}:{secs + ms_part/100:05.2f}"
        return f"{minutes:02d}:{secs + ms_part/100:05.2f}"
    
    def time_str_to_seconds(self, time_str: str, format_type: str = 'srt') -> float:
        if format_type == 'srt':
            match = self.srt_time_pattern.match(time_str.strip())
            if match:
                h, m, s, ms = map(int, match.groups())
                return h * 3600 + m * 60 + s + ms / 1000
        elif format_type == 'ass':
            match = self.ass_time_pattern.match(time_str.strip())
            if match:
                h, m, s, cs = map(int, match.groups())
                return h * 3600 + m * 60 + s + cs / 100
        elif format_type == 'lrc':
            parts = time_str.strip().split(':')
            if len(parts) == 2:
                m = int(parts[0])
                s = float(parts[1])
                return m * 60 + s
        return 0.0
    
    def parse_bcc(self, content: bytes) -> List[Dict]:
        try:
            data = json.loads(content.decode('utf-8'))
            if 'body' not in data or not isinstance(data['body'], list):
                raise ValueError("BCC格式错误，缺少body数组")
            subtitles = []
            for item in data['body']:
                from_time = item.get('from', 0.0)
                to_time = item.get('to', 0.0)
                text = item.get('content', '').strip()
                if text:
                    subtitles.append({'start': from_time, 'end': to_time, 'text': text})
            return subtitles
        except Exception as e:
            raise ValueError(f"解析BCC失败: {str(e)}")
    
    def parse_ass(self, content: bytes) -> List[Dict]:
        try:
            lines = content.decode('utf-8', errors='ignore').split('\n')
            subtitles = []
            in_dialogue = False
            for line in lines:
                line = line.strip()
                if line.startswith('[Events]'):
                    in_dialogue = True
                    continue
                if not in_dialogue or not line.startswith('Dialogue:'):
                    continue
                parts = line.split(',', 9)
                if len(parts) < 10:
                    continue
                start_time = self.time_str_to_seconds(parts[1], 'ass')
                end_time = self.time_str_to_seconds(parts[2], 'ass')
                text = parts[9].strip()
                text = re.sub(r'{[^}]*}', '', text)
                if text:
                    subtitles.append({'start': start_time, 'end': end_time, 'text': text})
            return subtitles
        except Exception as e:
            raise ValueError(f"解析ASS失败: {str(e)}")
    
    def parse_srt(self, content: bytes) -> List[Dict]:
        try:
            lines = content.decode('utf-8', errors='ignore').split('\n')
            subtitles = []
            current_sub = {'start': 0.0, 'end': 0.0, 'text': ''}
            in_text = False
            for line in lines:
                line = line.strip()
                if not line:
                    if current_sub['text']:
                        subtitles.append(current_sub)
                        current_sub = {'start': 0.0, 'end': 0.0, 'text': ''}
                    in_text = False
                    continue
                if '-->' in line and not in_text:
                    time_parts = line.split('-->')
                    if len(time_parts) == 2:
                        current_sub['start'] = self.time_str_to_seconds(time_parts[0], 'srt')
                        current_sub['end'] = self.time_str_to_seconds(time_parts[1], 'srt')
                    continue
                if not line.isdigit() and '-->' not in line:
                    current_sub['text'] += (line + '\n')
                    in_text = True
            if current_sub['text']:
                subtitles.append(current_sub)
            for sub in subtitles:
                sub['text'] = sub['text'].strip()
            return [s for s in subtitles if s['text']]
        except Exception as e:
            raise ValueError(f"解析SRT失败: {str(e)}")
    
    def parse_text(self, content: bytes) -> List[Dict]:
        try:
            lines = content.decode('utf-8', errors='ignore').split('\n')
            subtitles = []
            start_time = 0.0
            duration = 2.0
            for line in lines:
                line = line.strip()
                if line:
                    subtitles.append({'start': start_time, 'end': start_time + duration, 'text': line})
                    start_time += duration
            return subtitles
        except Exception as e:
            raise ValueError(f"解析TXT失败: {str(e)}")
    
    def detect_format(self, filename: str, content: bytes = None) -> str:
        ext = filename.split('.')[-1].lower()
        if ext == 'json' and content:
            try:
                data = json.loads(content.decode('utf-8'))
                if 'body' in data and isinstance(data['body'], list):
                    return 'bcc'
            except Exception:
                pass
            return 'json'
        return ext
    
    def generate_text(self, subtitles: List[Dict]) -> bytes:
        return '\n'.join([sub['text'] for sub in subtitles]).encode('utf-8')
    
    def generate_timed_text(self, subtitles: List[Dict]) -> bytes:
        lines = []
        for sub in subtitles:
            start_str = self.seconds_to_time_str(sub['start'], 'timed_text')
            end_str = self.seconds_to_time_str(sub['end'], 'timed_text')
            lines.append(f"[{start_str} --> {end_str}] {sub['text']}")
        return '\n'.join(lines).encode('utf-8')
    
    def generate_lrc(self, subtitles: List[Dict]) -> bytes:
        lines = []
        for sub in subtitles:
            time_str = self.seconds_to_time_str(sub['start'], 'lrc')
            lines.append(f"[{time_str}]{sub['text']}")
        return '\n'.join(lines).encode('utf-8')
    
    def generate_ass(self, subtitles: List[Dict]) -> bytes:
        ass_header = """[Script Info]
Title: Converted Subtitle
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,0
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines = [ass_header]
        for sub in subtitles:
            start_str = self.seconds_to_time_str(sub['start'], 'ass')
            end_str = self.seconds_to_time_str(sub['end'], 'ass')
            lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{sub['text']}")
        return '\n'.join(lines).encode('utf-8')
    
    def generate_srt(self, subtitles: List[Dict]) -> bytes:
        lines = []
        for idx, sub in enumerate(subtitles, 1):
            start_str = self.seconds_to_time_str(sub['start'], 'srt')
            end_str = self.seconds_to_time_str(sub['end'], 'srt')
            lines.append(str(idx))
            lines.append(f"{start_str} --> {end_str}")
            lines.append(sub['text'])
            lines.append('')
        return '\n'.join(lines).encode('utf-8')
    
    def convert_file(
        self, filename: str, content: bytes,
        output_formats: List[str], name_mode: str = 'original'
    ) -> Dict[str, Any]:
        """转换单个文件"""
        try:
            input_format = self.detect_format(filename, content)
            
            if input_format in ('bcc', 'json'):
                subtitles = self.parse_bcc(content)
            elif input_format == 'ass':
                subtitles = self.parse_ass(content)
            elif input_format == 'srt':
                subtitles = self.parse_srt(content)
            elif input_format == 'txt':
                subtitles = self.parse_text(content)
            else:
                return {'success': False, 'message': f"不支持的输入格式: {input_format}", 'output_files': []}
            
            if not subtitles:
                return {'success': False, 'message': '未解析到字幕内容', 'output_files': []}
            
            output_files = []
            base_name = '.'.join(filename.split('.')[:-1]) if '.' in filename else filename
            
            if name_mode == 'date':
                date_str = datetime.now().strftime('%Y%m%d_%H%M%S')
                base_name = f"{base_name}_{date_str}"
            
            format_map = {
                'text': ('纯文字.txt', self.generate_text),
                'timed_text': ('带时间轴.txt', self.generate_timed_text),
                'lrc': ('.lrc', self.generate_lrc),
                'ass': ('.ass', self.generate_ass),
                'srt': ('.srt', self.generate_srt)
            }
            
            for fmt in output_formats:
                if fmt not in format_map:
                    continue
                suffix, generator = format_map[fmt]
                if ' ' in suffix:
                    output_filename = f"{base_name}_{suffix}"
                else:
                    output_filename = f"{base_name}{suffix}"
                output_content = generator(subtitles)
                output_files.append({
                    'format': fmt,
                    'filename': output_filename,
                    'content': output_content
                })
            
            return {
                'success': True,
                'message': f"成功转换为 {len(output_files)} 种格式",
                'output_files': output_files
            }
        except Exception as e:
            return {'success': False, 'message': f"转换失败: {str(e)}", 'output_files': []}

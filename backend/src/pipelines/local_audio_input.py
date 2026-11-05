"""Bounded local M4A and Ogg/Opus selection. Never fetch media or change originals."""
from __future__ import annotations

import io
import json
import math
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

import numpy as np
import soundfile as sf
import structlog

MAX_UPLOAD_BYTES = 512 * 1024**2
EMPTY_AUDIO_MESSAGE = '選択した音源は0バイトの空ファイルです。音声データのあるファイルを選択してください。'
SUPPORTED_EXTENSIONS = {'.wav', '.flac', '.m4a', '.opus', '.ogg'}
COMPRESSED_EXTENSIONS = {'.m4a', '.opus', '.ogg'}
_DECODER_SLOT = threading.BoundedSemaphore(1)
logger = structlog.get_logger()


@dataclass(frozen=True)
class DecoderFailure:
    """Local-only decoder evidence; never serialize stderr in an HTTP response."""
    stage: Literal['probe', 'decode']
    returncode: int | None
    stderr: str
    diagnostic_id: str = field(default_factory=lambda: str(uuid4()))


def _private_stderr(data: bytes, command: list[str]) -> str:
    """Keep bounded decoder evidence in the local log without paths or URLs."""
    text=data[:8192].decode('utf-8', errors='replace')
    for argument in command:
        if Path(argument).is_absolute():
            text=text.replace(argument, '<local-file>')
    text=re.sub(r'https?://\S+', '<redacted-url>', text)
    text=re.sub(r'/tmp/stem2tab-audio-[^/\s]+', '<work>', text)
    return text


class AudioSelectionError(Exception):
    """A public boundary failure, with no decoder log or local path exposure."""
    def __init__(self, status: int, code: str, message: str, *, failure: DecoderFailure | None = None):
        self.status, self.code, self.message = status, code, message
        self.failure = failure
        super().__init__(code)


def _run(command: list[str], timeout: int, *, stage: Literal['probe', 'decode']) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except FileNotFoundError:
        raise AudioSelectionError(503, 'decoder_missing', 'この形式にはサーバー上のffmpegとffprobeが必要です。自動インストールは行いません。') from None
    except subprocess.TimeoutExpired:
        raise AudioSelectionError(422, 'decode_timeout', '音源のローカル変換が制限時間を超えました。短い音源またはWAV/FLACを使用してください。') from None
    if result.returncode:
        label = '音源情報の読み取り（ffprobe）' if stage == 'probe' else 'PCMへの変換（ffmpeg）'
        # Keep bounded evidence for an explicitly invoked local diagnostic. The
        # API continues to expose only the public message, never raw stderr.
        failure = DecoderFailure(stage, result.returncode, _private_stderr(result.stderr, command))
        logger.warning('local_audio_command_failed', diagnostic_id=failure.diagnostic_id,
                       stage=stage, returncode=failure.returncode, stderr=failure.stderr)
        raise AudioSelectionError(422, 'audio_decode_failed',
            f'{label}に失敗しました。終了コード{result.returncode}。原因はローカル診断記録で確認します。採譜は開始していません。診断ID: {failure.diagnostic_id}',
            failure=failure)
    return result


def decode_selection(data: bytes, extension: str, start: float, end: float | None,
                     *, max_seconds: int) -> tuple[bytes, dict]:
    """Select decoded PCM samples, after codec delay/skip handling by local FFmpeg.

    atrim counts decoded samples instead of seeking compressed packets. Output
    is bounded even for misleading duration metadata; a too-long full selection
    is rejected, never silently truncated. No codec correction is inferred.
    """
    if not data:
        raise AudioSelectionError(422, 'empty_audio', EMPTY_AUDIO_MESSAGE)
    if len(data) > MAX_UPLOAD_BYTES:
        raise AudioSelectionError(413, 'upload_limit', '最大アップロードサイズは512 MiBです。')
    if extension not in COMPRESSED_EXTENSIONS:
        raise AudioSelectionError(415, 'unsupported_audio', 'M4AまたはOgg/Opusを選択してください。')
    if not math.isfinite(start) or start < 0 or (end is not None and (not math.isfinite(end) or end <= start)):
        raise AudioSelectionError(422, 'invalid_selection', '開始・終了位置が不正です。音源は切り詰めていません。')
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise AudioSelectionError(503, 'decoder_missing', 'この形式にはサーバー上のffmpegとffprobeが必要です。自動インストールは行いません。')
    if not _DECODER_SLOT.acquire(blocking=False):
        raise AudioSelectionError(409, 'decoder_busy', '別の音源をローカル変換中です。完了後にもう一度選択してください。')
    try:
        with tempfile.TemporaryDirectory(prefix='stem2tab-audio-') as temporary:
            source = Path(temporary)/('input'+extension)
            source.write_bytes(data)
            output = Path(temporary)/'selection.wav'
            demux = 'mov' if extension == '.m4a' else 'ogg'
            options = ['-protocol_whitelist', 'file', '-f', demux]
            if demux == 'mov':
                options += ['-enable_drefs', '0']
            probe = _run([ffprobe, '-v', 'error', *options, '-show_entries',
                          'stream=codec_type,codec_name,sample_rate,channels:format=format_name,duration',
                          '-of', 'json', str(source)], 15, stage='probe')
            try:
                metadata = json.loads(probe.stdout)
                streams = [s for s in metadata['streams'] if s.get('codec_type') == 'audio']
                if len(streams) != 1:
                    raise ValueError('One explicit audio stream is required')
                stream = streams[0]
                codec = stream['codec_name']
                sr, channels = int(stream['sample_rate']), int(stream['channels'])
                duration = float(metadata['format']['duration'])
                if not math.isfinite(duration) or duration <= 0 or channels not in (1, 2) or not 8000 <= sr <= 96000:
                    raise ValueError('Unsupported decoded audio geometry')
            except (KeyError, TypeError, ValueError):
                raise AudioSelectionError(422, 'invalid_audio_metadata', '音源の長さ・チャンネル・サンプルレートを確認できません。') from None
            if (demux == 'mov' and codec not in {'aac', 'alac'}) or (demux == 'ogg' and codec != 'opus'):
                raise AudioSelectionError(415, 'unsupported_codec', 'M4AはAAC/ALAC、.opus/.oggはOggコンテナのOpusに対応しています。')
            if start >= duration or (end is not None and end > duration + 1/sr):
                raise AudioSelectionError(422, 'invalid_selection', '指定区間が音源の範囲外です。')
            a = round(start*sr)
            b = None if end is None else round(end*sr)
            if b is not None and not 0 < b-a <= max_seconds*sr:
                raise AudioSelectionError(422, 'duration_limit', f'処理区間は{max_seconds}秒以内を明示してください。')
            # Container duration may include small codec padding; actual PCM is
            # checked below. This guard avoids decoding clearly oversized full input.
            if b is None and duration-start > max_seconds+1:
                raise AudioSelectionError(422, 'duration_limit', f'処理区間は{max_seconds}秒以内を明示してください。')
            trim = f'atrim=start_sample={a}' + (f':end_sample={b}' if b is not None else '')
            byte_cap = (max_seconds+1)*sr*channels*4+65536
            _run([ffmpeg, '-nostdin', '-v', 'error', *options, '-threads', '1', '-i', str(source),
                  '-map', '0:a:0', '-vn', '-sn', '-dn', '-map_metadata', '-1', '-threads', '1',
                  '-af', trim+',asetpts=PTS-STARTPTS', '-t', str(max_seconds+1), '-fs', str(byte_cap),
                  '-c:a', 'pcm_f32le', '-ar', str(sr), '-ac', str(channels),
                  '-fflags', '+bitexact', '-flags:a', '+bitexact', str(output)], 60, stage='decode')
            try:
                with sf.SoundFile(output) as audio:
                    frames = len(audio)
                    if audio.samplerate != sr or audio.channels != channels or frames <= 0:
                        raise ValueError('Decoded geometry differs')
                    if frames > max_seconds*sr:
                        raise AudioSelectionError(422, 'duration_limit', f'変換後の処理区間が{max_seconds}秒を超えています。切り詰めて続行していません。')
                    if b is not None and frames != b-a:
                        raise AudioSelectionError(422, 'incomplete_selection', '指定区間のPCMサンプルを最後まで取得できませんでした。')
                    if not np.isfinite(audio.read(dtype='float32', always_2d=True)).all():
                        raise ValueError('Nonfinite decoded samples')
            except (OSError, RuntimeError, ValueError):
                raise AudioSelectionError(422, 'invalid_decoded_audio', '変換後の音源を検証できませんでした。') from None
            full_decode = a == 0 and b is None
            selection = dict(source_duration_seconds=frames/sr if full_decode else duration,
                source_duration_basis='decoded_pcm' if full_decode else 'container_estimate',
                source_container_duration_seconds=duration, source_format='M4A' if demux == 'mov' else 'OGG',
                source_codec=codec, source_extension=extension, decoder='ffmpeg-local',
                start_frame=a, end_frame=a+frames, sample_rate=sr, selected_seconds=frames/sr,
                note_time_origin='selection_start', source_origin_seconds=a/sr)
            return output.read_bytes(), selection
    except OSError:
        raise AudioSelectionError(503, 'decoder_runtime_unavailable', 'ローカル変換の実行環境または作業領域を使用できません。採譜は開始していません。') from None
    finally:
        _DECODER_SLOT.release()

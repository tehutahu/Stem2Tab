"""Real synthetic audio checks, without model assets or checked-in recordings."""
import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from src.evaluation.audio_tempo import AudioInfo, TempoAudioError, analysis_intervals, estimate_audio_tempo, inspect_audio
from src.evaluation.audio_tempo_synthetic import pulse_audio, synthetic_audio_cases


def native(samples, rate=16000, format="WAV"):
    stream=io.BytesIO();sf.write(stream,samples,rate,format=format,subtype="PCM_16" if format=="FLAC" else "FLOAT")
    return stream.getvalue()


@pytest.mark.parametrize("case",list(synthetic_audio_cases()),ids=lambda case:case.name)
def test_known_synthetic_audio_and_honest_negative_controls(case):
    data=case.wav_bytes()
    stream=io.BytesIO(data)
    result=estimate_audio_tempo(stream)
    assert data==stream.getvalue()
    assert result.method.quality_calibration=="uncalibrated"
    assert result.method.bpm_min==40 and result.method.bpm_max==240
    assert sum(i.end_seconds-i.start_seconds for i in result.analyzed_intervals)<=60+1e-8
    assert 1<=len(result.analyzed_intervals)<=3 and 1<=len(result.windows)<=6
    assert all(window.start_seconds<window.end_seconds for window in result.windows)
    if case.expected_bpm is not None:
        assert result.status=="candidates",result
        assert any(abs(candidate.bpm-case.expected_bpm)/case.expected_bpm<.025 for candidate in result.candidates)
        assert result.candidates[0].supporting_windows<=len(result.windows)
        assert result.candidates[0].periodicity is not None
        assert all(c.periodicity is None and c.supporting_windows==0 for c in result.candidates if c.relation in {"half","double"})
    elif case.expected_status=="noncandidate":
        assert result.status!="candidates" and not result.candidates
    else:
        assert result.status==case.expected_status and not result.candidates


@pytest.mark.parametrize("seconds",[8,8.1,9,10,11.99,12,13,20,21,36,59,60,61,120])
def test_short_supported_and_long_sample_plans_disclose_all_analyzed_ranges(seconds):
    result=estimate_audio_tempo(io.BytesIO(native(pulse_audio(bpm=120,seconds=seconds,rate=8000),8000)))
    assert result.status=="candidates",result
    assert abs(result.candidates[0].bpm-120)<.5
    assert result.coverage==("sampled" if seconds>60 else "full")
    assert result.analyzed_intervals[0].start_seconds==0
    assert result.analyzed_intervals[-1].end_seconds==seconds
    assert all(window.end_seconds-window.start_seconds>=6 for window in result.windows)
    if result.coverage=="full":
        assert sum(i.end_seconds-i.start_seconds for i in result.analyzed_intervals)==pytest.approx(seconds)
    else:
        assert sum(i.end_seconds-i.start_seconds for i in result.analyzed_intervals)==pytest.approx(60)


@pytest.mark.parametrize("channels,rate",[(1,8000),(2,44100),(2,96000)])
def test_native_formats_rates_and_no_stereo_phase_cancellation(channels,rate):
    samples=pulse_audio(bpm=90,seconds=12,rate=rate)
    if channels==2:samples=np.column_stack((samples,-samples))
    for format in ("WAV","FLAC"):
        data=native(samples,rate,format)
        info=inspect_audio(io.BytesIO(data),max_seconds=120)
        assert info.channels==channels and info.sample_rate==rate and info.format==format
        result=estimate_audio_tempo(io.BytesIO(data))
        assert result.status=="candidates" and any(abs(c.bpm-90)<1 for c in result.candidates)


def test_sampling_plan_maximum_is_bounded_before_decoding():
    info=AudioInfo(96000,2,600*96000,"WAV")
    intervals=analysis_intervals(info)
    assert [(i.start_seconds,i.end_seconds) for i in intervals]==[(0,20),(290,310),(580,600)]


@pytest.mark.parametrize("samples,rate",[(np.zeros((100,3)),8000),(np.zeros(100),4000),(np.zeros(121*8000),8000),(np.zeros(0),8000)])
def test_rejects_unsupported_metadata_before_analysis(samples,rate):
    data=native(samples,rate)
    with pytest.raises(TempoAudioError):estimate_audio_tempo(io.BytesIO(data))


@pytest.mark.parametrize("data",[b"",b"RIFFbad",b"not audio"])
def test_malformed_native_audio_is_a_safe_refusal(data):
    with pytest.raises(TempoAudioError,match="malformed|unsupported"):
        estimate_audio_tempo(io.BytesIO(data))


def test_nonfinite_decoded_samples_and_cooperative_budget_refuse():
    samples=pulse_audio(bpm=120,seconds=12);samples[40]=np.nan
    with pytest.raises(TempoAudioError,match="nonfinite"):
        estimate_audio_tempo(io.BytesIO(native(samples)))
    with pytest.raises(TempoAudioError,match="cooperative"):
        estimate_audio_tempo(io.BytesIO(native(pulse_audio(bpm=120,seconds=12))),budget_seconds=-1)

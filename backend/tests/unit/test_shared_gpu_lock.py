"""Independent API data roots share a configured server-side GPU lock path."""
import os
from pathlib import Path

from fastapi import HTTPException
import pytest

from src.api import muscriptor_trials as trial


def test_shared_path_excludes_default_old_server_in_both_directions(tmp_path,monkeypatch):
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    monkeypatch.delenv('STEM2TAB_GPU_LOCK_PATH',raising=False)
    first=trial._acquire_slot(old)
    try:
        monkeypatch.setenv('STEM2TAB_GPU_LOCK_PATH',str(old/'execution.lock'))
        with pytest.raises(HTTPException) as caught:trial._acquire_slot(new)
        assert caught.value.status_code==409 and caught.value.detail['code']=='trial_busy'
    finally:os.close(first)
    second=trial._acquire_slot(new)
    try:
        monkeypatch.delenv('STEM2TAB_GPU_LOCK_PATH')
        with pytest.raises(HTTPException) as caught:trial._acquire_slot(old)
        assert caught.value.status_code==409
        assert not (new/'execution.lock').exists()
    finally:os.close(second)
    os.close(trial._acquire_slot(old))


@pytest.mark.parametrize('kind',['relative','missing','directory','symlink'])
def test_invalid_shared_path_fails_without_creating_or_following_it(tmp_path,monkeypatch,kind):
    base=tmp_path/'data';base.mkdir()
    path=tmp_path/'control.lock'
    if kind=='relative':path=Path('relative.lock')
    elif kind=='directory':path.mkdir()
    elif kind=='symlink':
        target=tmp_path/'target';target.write_bytes(b'preserved');path.symlink_to(target)
    monkeypatch.setenv('STEM2TAB_GPU_LOCK_PATH',str(path))
    with pytest.raises(HTTPException) as caught:trial._acquire_slot(base)
    assert caught.value.status_code==503 and caught.value.detail['code']=='gpu_lock_unavailable'
    assert not (base/'execution.lock').exists()
    if kind=='missing':assert not path.exists()
    if kind=='symlink':assert path.is_symlink() and target.read_bytes()==b'preserved'


@pytest.mark.parametrize(('shared', 'failure'), [
    (False, 'flock'), (False, 'busy'), (True, 'fstat'),
    (True, 'flock'), (True, 'busy'), (True, 'post_stat'),
])
def test_acquisition_failure_closes_opened_descriptor(tmp_path, monkeypatch, shared, failure):
    import errno
    import fcntl

    path = tmp_path / 'shared.lock'
    path.touch()
    if shared:
        monkeypatch.setenv('STEM2TAB_GPU_LOCK_PATH', str(path))
    else:
        monkeypatch.delenv('STEM2TAB_GPU_LOCK_PATH', raising=False)
    opened = []
    closed = []
    real_open, real_close, real_stat = os.open, os.close, Path.stat

    def track_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def track_close(descriptor):
        closed.append(descriptor)
        real_close(descriptor)

    def fail(*args, **kwargs):
        if failure == 'busy':
            raise BlockingIOError(errno.EWOULDBLOCK, 'injected contention')
        raise OSError(errno.EIO, 'injected IO failure')

    def stat_after_open(self, *args, **kwargs):
        if opened:
            return fail()
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(os, 'open', track_open)
    monkeypatch.setattr(os, 'close', track_close)
    if failure == 'fstat':
        monkeypatch.setattr(os, 'fstat', fail)
    elif failure == 'post_stat':
        monkeypatch.setattr(Path, 'stat', stat_after_open)
    else:
        monkeypatch.setattr(fcntl, 'flock', fail)
    with pytest.raises(HTTPException) as caught:
        trial._acquire_slot(tmp_path)
    assert caught.value.status_code == (409 if failure == 'busy' else 503)
    assert len(opened) == 1
    assert closed == opened
    with pytest.raises(OSError) as closed_fd:
        os.lseek(opened[0], 0, os.SEEK_CUR)
    assert closed_fd.value.errno == errno.EBADF


@pytest.mark.parametrize('timing', ['before_open', 'after_flock'])
@pytest.mark.parametrize('replacement', ['file', 'parent_symlink'])
def test_shared_path_change_during_acquisition_is_rejected(tmp_path, monkeypatch, timing, replacement):
    import fcntl

    control = tmp_path / 'control'
    control.mkdir()
    path = control / 'gpu.lock'
    path.touch()
    monkeypatch.setenv('STEM2TAB_GPU_LOCK_PATH', str(path))
    real_open, real_flock = os.open, fcntl.flock
    opened = []

    def replace_path():
        if replacement == 'file':
            path.rename(control / 'old.lock')
            path.write_bytes(b'replacement')
        else:
            alternate = tmp_path / 'alternate'
            alternate.mkdir()
            (alternate / 'gpu.lock').write_bytes(b'alternate')
            control.rename(tmp_path / 'old-control')
            control.symlink_to(alternate, target_is_directory=True)

    def open_with_replacement(*args, **kwargs):
        if timing == 'before_open':
            replace_path()
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def flock_with_replacement(*args):
        real_flock(*args)
        if timing == 'after_flock':
            replace_path()

    monkeypatch.setattr(os, 'open', open_with_replacement)
    monkeypatch.setattr(fcntl, 'flock', flock_with_replacement)
    with pytest.raises(HTTPException) as caught:
        trial._acquire_slot(tmp_path)
    assert caught.value.status_code == 503
    assert caught.value.detail['code'] == 'gpu_lock_unavailable'
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.parametrize('failure', ['stat', 'resolve', 'resolve_loop'])
def test_shared_path_validation_failure_is_structured(tmp_path, monkeypatch, failure):
    path = tmp_path / 'shared.lock'
    path.touch()
    monkeypatch.setenv('STEM2TAB_GPU_LOCK_PATH', str(path))

    def fail(*args, **kwargs):
        if failure == 'resolve_loop':
            raise RuntimeError('Symlink loop')
        raise OSError('injected validation failure')

    monkeypatch.setattr(Path, 'stat' if failure == 'stat' else 'resolve', fail)
    with pytest.raises(HTTPException) as caught:
        trial._acquire_slot(tmp_path)
    assert caught.value.status_code == 503
    assert caught.value.detail['code'] == 'gpu_lock_unavailable'
    monkeypatch.undo()
    assert not (tmp_path / 'execution.lock').exists()

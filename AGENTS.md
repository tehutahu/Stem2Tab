# Stem2Tab Agent Guide

## Current product priority

- Prioritize measurable transcription quality over further Web UI expansion.
- The benchmark CLI under `backend/src/evaluation/` is the current evaluation entry point.
- No trusted ground-truth/reference MIDI is currently available for the project.
- Treat listening comparison as the default workflow: `--reference` must remain optional.
- Only calculate note-accuracy metrics when the user supplies a bass-only reference MIDI.
- Do not download or commit public evaluation datasets until licensing, redistribution, size, and coverage have been agreed.

## Repository map

- `backend/src/evaluation/`: benchmark CLI, canonical note events, adapters, metrics, and reports.
- `backend/src/pipelines/`: the existing Demucs, Basic Pitch, and TAB-generation paths used by the Web worker.
- `backend/src/api/` and `backend/src/worker/`: FastAPI and Celery application flow.
- `backend/tests/`: unit and integration tests.
- `frontend/`: React/Vite UI.
- `docs/ROADMAP.md`: source of truth for implementation order.
- `docs/TESTING.md`: source of truth for test and benchmark procedures.

## Architecture rules

- Keep separation, transcription, note processing, rhythm/quantization, and notation concerns decoupled.
- Add new separators and transcribers through the adapter protocols and registries; do not hard-code them into the benchmark runner.
- Preserve raw backend output alongside canonical JSON/CSV and Performance MIDI artifacts.
- Do not treat Basic Pitch output as final Score MIDI.
- Score MIDI, beat quantization, and TAB fingering optimization belong to later phases.
- Do not change the current Web worker pipeline while evaluating a candidate backend unless the task explicitly includes that integration.

## Python and backend conventions

- Support Python 3.11 or newer and use type hints for public functions.
- Use `pathlib.Path` for filesystem paths.
- Add docstrings to public functions and classes.
- Use Pydantic v2 for validated public data models.
- Use `structlog` for service/runtime logs. Normal CLI report output may be written to stdout.
- Do not swallow exceptions. Adapter failures should be recorded and other benchmark combinations should continue.
- Keep generated benchmark artifacts under `benchmark_results/` or another ignored output directory.

## Dependency policy

- Use `uv` for Python dependency management; do not use ad-hoc `pip install`.
- Basic Pitch is installed with `backend/scripts/install_basic_pitch.sh` using `--no-deps`.
- Do not add Basic Pitch to `pyproject.toml` or `uv.lock`, and do not add TensorFlow.
- Ask for confirmation before adding any new production dependency.
- Prefer `pnpm` when adding frontend dependencies.

## Testing requirements

- Mock heavy external backends such as Demucs and Basic Pitch in unit tests.
- Keep one focused real Basic Pitch ONNX integration smoke test.
- For evaluation changes, run all `test_evaluation_*.py` unit modules.
- Run relevant existing backend tests after changing shared pipeline code.
- Always run `npm test` after modifying JavaScript or TypeScript files.
- Do not require copyrighted audio or checked-in binary reference MIDI for tests; synthesize small temporary fixtures.

## Git and documentation

- Keep commits scoped and avoid modifying unrelated user changes.
- Update `README.md` and `docs/TESTING.md` when CLI arguments, artifacts, or evaluation assumptions change.
- Keep the current lack of trusted reference MIDI explicit until such data is actually obtained and validated.

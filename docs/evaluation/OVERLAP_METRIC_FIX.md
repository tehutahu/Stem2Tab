# Short-note overlap metric correction

Branch `fix/evaluation-disjoint-overlap`, based on main `77e4061`. PR #6 and its
inference/note assembly settings remain unchanged. No dataset is included.

## Reproduction and behavior

A reference note `[.1, .2]` and an estimate `[.06, .08]` at the same pitch match
under the existing 50ms onset tolerance despite being disjoint. mir_eval 0.8.2
computes a negative overlap numerator, yielding `-.142857...`. The benchmark's
0–1 metric validation then fails, so the CLI returns an error after inference.
The regression test reproduced this before the fix, including the CLI exit code.

Keep the same mir_eval onset/pitch matching and all onset/offset tolerances.
For each matched pair compute `max(0, min(end) - max(start))` divided by the span
from earliest onset to latest offset, then average **all** matched pairs.
A disjoint or touching pair contributes zero; it is not dropped. No matches
still means zero. Do not floor the entire mean, which would preserve negative
contributions in mixed cases. All overlapping-pair ratios retain their formula.

Onset, onset+offset, frame, extra/missed, octave, and duration metrics retain
their definitions and matching. This is an evaluation correction, not an
improvement in transcription. The JSON field name and 0–1 validation remain.

## Fixed-output replay and review

The separately saved synthetic technique-C experiment contains 44 cases and
132 predictions. Replay uses their existing reference MIDI and estimated events;
no inference, parameter search, new audio, or model acquisition is needed.

- Before: 123 successful metrics and 9 `average_overlap_ratio` validation errors.
- After: 132/132 successful metrics; all 9 formerly failing cases also complete
  the benchmark CLI using a replay adapter with saved events.
- Independent review captured pre-validation values from the old evaluator and
  verified **every other metric field is identical on all 132 results**.
- AOR changes on 21 results: the 9 former errors and 12 former successes with
  mixed overlapping/disjoint pairs. On the other 111 results, AOR is unchanged.
  Existing mixed-case AOR numbers therefore should not be compared as if their
  calculation were unchanged. F1/count comparisons remain valid.

Local evidence (ignored, not redistributed):
`backend/benchmark_results/overlap-fix-20261022/replay.py`,
`replay_results.json`, and `cli/`. Original C artifacts in the separate PR #6
worktree remain intact; the replay path does not overwrite them. The saved
pYIN predictions demonstrate the problem but the fix and regression tests do
not require pYIN, Basic Pitch, external recordings, or model inference.

## Validation

New unit cases cover early/late disjoint notes, touching endpoints, partial
intersection, mixed exact/disjoint notes, no pitch match, and empty estimates.
The CLI regression checks successful report/metrics artifacts while preserving
an onset true positive and an offset failure. Before implementation: 3 metric
regressions and the CLI test failed. After implementation: all evaluation unit
modules **34 passed**; complete main-based backend suite **68 passed** with four
existing dependency deprecation warnings. An independent reviewer also ran the
34 evaluation tests and replayed the 132 saved predictions, with no blocking
findings. No production pipeline, adapter default, dependency, or lock changed.

As an additional compatibility check, the PR #6 worktree's 141 tests also passed
(four existing warnings) with only the fixed metrics module loaded **in memory**.
No PR #6 file or commit was modified; this is not a merge or a second inference
experiment. The branch's normal full test result remains 68 tests, since it is
based directly on main and contains none of PR #6's pYIN additions.

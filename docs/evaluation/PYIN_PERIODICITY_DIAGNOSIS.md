# Periodic-continuity diagnosis: rejected onset veto

The fixed periodic-continuity candidate is **not adopted**. Eight removed splits give a small aggregate offset-score gain, but one correct reference onset disappears and two independent synthetic repick conditions lose two notes each. No threshold was adjusted after these results. pYIN, note assembly, the evaluation adapter registry, raw output and production defaults are unchanged. This is a diagnostic experiment, not an optional registered transcriber.

## Frozen hypothesis and criteria

PR #9 rejected RMS-rise and activity removal. This experiment instead tests waveform periodic continuity: an attack boundary might be a sustain fluctuation if the waveform continues almost unchanged for several F0 periods. It does not treat periodicity as proof that no repick occurred.

For each existing onset, obtain the nearest saved F0 and require at least three frame starts within ±one period. Every frame in that range must remain voiced, active and within 50 cents of that F0. Compare two-period audio windows centered at onset−2 periods, onset and onset+2 periods, against their one-period-lagged waveform. Subtract each window mean; compute normalized correlation using fractional-sample linear interpolation, with sample bounds rounded to nearest integer. Veto only if all three correlations are at least **0.98**. Missing samples, invalid F0 or norm ≤1e−20 preserve the onset. Period length, all correlations and decision reasons are retained privately. No RMS gate is used.

The nearest saved F0 determines one fixed period; no lag search or learned threshold is used. The three-frame eligibility rule makes short periods conservative under the existing 256-sample hop. Removing an onset can extend the preceding note to a run end or next retained onset. Voicing/activity gaps remain invalid frames and are not filled. However, existing assembly also uses onsets to protect one-frame pitch excursions from smoothing: changing the onset list can change smoothing eligibility and pitch-run boundaries. Therefore a general guarantee of unchanged pitch-run support is not established by this gate. This is another reason not to adopt it, and not a blanket extension rule.

Before outcome inspection, one candidate and eleven new synthetic conditions were frozen in `pyin_periodicity_plan.json`. Adoption required: no lost baseline reference IDs overall or per odd track; no recall reduction; strictly higher onset+offset F1; non-increasing paired offset MAE; and no case-specific extra/missed/offset-score regression in the new synthetic conditions. Matching identities, rather than only counts, are compared. The code hash in the frozen plan matches the evaluated candidate source. An initial output stopped before metrics while serializing a NumPy boolean; only its Python boolean serialization was corrected, and the failed output was retained separately. Numeric rules and cases were unchanged.

## Odd-recording outcome

Only the nine odd recordings, 500 reference notes, were used. The even eight were excluded from audio/annotation inspection, inference, scoring and selection. Preservation hash checks do not parse their content. Baseline assembly is reconstructed with the actual resampled duration and exactly reproduces all 991 saved baseline events after MIDI roundtrip.

| Metric | Baseline | Periodicity veto |
| --- | ---: | ---: |
| Estimated notes | 991 | 983 |
| Matched notes | 386 | 385 |
| Precision | 0.389506 | 0.391658 |
| Recall | 0.772 | 0.770 |
| Extra / missed | 605 / 114 | 598 / 115 |
| Onset F1 | 0.517773 | 0.519218 |
| Onset+offset F1 | 0.344735 | 0.347943 |
| Frame F1 (track macro) | 0.738617 | 0.738617 |

Eight onset vetoes remove eight boundaries. One baseline-matched reference identity is lost, none is rescued; its stratum is a long normal-expression fingerstyle note. This violates the predeclared safety condition even though aggregate F1 rises slightly. On the common 385 reference IDs, offset MAE falls 117.174→116.429 ms, with signed mean −99.112→−98.026 ms. Common onset MAE remains 20.548 ms. These small conditional offset gains do not compensate for losing a real note. Independent replay confirms each of the eight changed odd-recording intervals equals the union of its same-pitch baseline intervals: no new time is filled in these measured cases. Frame occupancy is therefore unchanged, illustrating why frame F1 cannot expose the note-boundary loss. Separately, a new frame-level rounding-boundary counterexample (33.49/33.51 MIDI before rounding, all voiced/active) loses onset protection and fills a 10 ms pitch-run gap after smoothing. That demonstrates a general applicability limit; it is not claimed to have happened in the eight observed merges.

The development CSV retains overall signed onset/offset errors; paired CSV fixes reference identities. Track-change CSV reports only counts, not private IDs. Strata report recall and signed timing by expression, excitation and duration; extras have no reliable reference-style label, so style precision is not fabricated. Candidate assembly/MIDI time is in the synthetic CSV. Runtime CSV separately summarizes candidate assembly/MIDI time for nine odd and eleven synthetic cases; all completed. Fresh pYIN inference time and a fair baseline runtime comparison were not measured. Metric runtime is zero for saved-frame evaluation and is not an inference-speed claim.

## Independent synthetic counterexamples

Eleven newly generated conditions use different pitches, timings, partials and envelopes from PR #9: 45/90 ms rests, short low notes, equal repicks, softer repicks, phase-continuous falling and rising repicks, legato changes, weak release, vibrato and silence. Waveforms and reference MIDI remain private. Nominal envelope endpoints do not assert perceptual offset under very weak PCM16 tails.

Both phase-continuous repick conditions change three baseline notes into one: onset recall 1→1/3, onset F1 1→0.5 and onset+offset F1 1→0. The rising case is an amplitude increase, so this is a different failure from the prior RMS-rise gate. Small same-phase amplitude steps preserve high normalized correlation: the signal can be periodic while a nominal excitation boundary exists in the generated reference and is independently detected. These idealized phase-continuous envelope changes isolate a boundary ambiguity; they are not a complete physical model of a bass-string repick. The deterministic frame-level test isolates the same failure without pYIN detection uncertainty.

The other nine conditions produce identical baseline/candidate notes. Rest and short-note cases pass, but unchanged softer-repick (four extras), legato (four extras and one miss), and equal-repick offset failures remain detector/assembly limits. Silence has undefined note metrics. Passing easy cases does not validate the unsafe repick veto. There is no adopted candidate, no even-track confirmation evaluation and no additional threshold search.

## Verification and reproduction

Uses the existing CPU environment, no new dependency/model, API cost or GPU change. Evaluation unit modules plus TAB roundtrip: **153 passed**, with three existing audio-reader deprecation warnings. All 27 older synthetic saved-frame replays match their baseline events exactly; this is assembly replay, not fresh model inference. All 809 original baseline manifest hashes match. Original main's uncommitted status and tracked-diff hashes are unchanged; PR #8 and PR #9 worktrees remain clean at their fixed heads. Independent pre-plan and post-result review reproduced the eight unions, lost reference identity, synthetic regressions and rounding-boundary counterexample. Public files contain only code, procedures and aggregates; no blocking findings remain after restricting the guarantee to voiced/activity gaps.

Starting from PR #9 `2edd8a975ee14fad35f9163888f3256f0e766b44`, from backend, using the already installed environment and a fresh ignored results directory:

```sh
PYTHONPATH=. /path/to/existing/venv/bin/python scripts/pyin_periodicity_diagnostics.py --freeze-plan benchmark_results/new-private-periodicity
CUDA_VISIBLE_DEVICES='' PYTHONPATH=. /path/to/existing/venv/bin/python scripts/run_pyin_periodicity_study.py --baseline-root /private/validated-idmt-baseline --results benchmark_results/new-private-periodicity
PYTHONPATH=. /path/to/existing/venv/bin/python -m pytest tests/unit/test_evaluation_*.py tests/unit/test_tab_roundtrip.py -q
```

The driver reads only odd `dataset/audio`, `references` and saved `baseline/*/runs/direct__pyin` paths. It retains private onset decisions, changed boundaries, reference-ID comparisons, raw frames, raw MIDI, candidate MIDI, frozen source hash and per-case progress. Each new synthetic audio is inferred once, then both assemblies share the exact same saved frames. The published driver only replaces private local path constants with explicit CLI arguments; numeric experiment logic is unchanged.

Scoring retains onset 50 ms, pitch 50 cents, offset max(50 ms, 20% reference duration), and estimate-minus-reference signed errors. This experiment is distinct from Basic Pitch and synthesized accuracy must not be substituted for recorded-audio results. The validated archive's 948 events are retained despite the paper's unexplained 941 total. Official CC BY-NC-ND 4.0 and bundled CC BY-SA 4.0 remain inconsistent: audio and annotations stay private for internal noncommercial evaluation.

## Evidence needed next

Neither amplitude rise nor near-perfect periodicity distinguishes a weak same-pitch excitation from sustain reliably. A next proposal needs a cue specific to excitation, such as a localized nonharmonic attack residual or transient-noise pattern, tested against vibrato/partial-envelope motion and quiet same-phase repicks. It must show that it preserves true excitation boundaries on the development set before deleting splits; high correlation alone is insufficient. For offset recovery across unvoiced gaps, separate evidence is needed that acoustic pitch/energy persists through the gap without filling real rests. These are future evidence requirements, not tested improvements. No new model, annotation change, manual exception for the lost note or tuned threshold is introduced here.

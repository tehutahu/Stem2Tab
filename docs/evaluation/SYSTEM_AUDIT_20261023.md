# Full-chain development audit, 2026-10-23

This audit finds no safe new acoustic correction to adopt. It adds an opt-in notation integrity check so output-format failures cannot be mistaken for upstream transcription accuracy. Defaults and raw transcription artifacts are retained.

## Scope and protected data

PR8 `aca357ab9f2c38f0bb36e954e3bb5e1c1b1157b7` is the fixed code base. IDMT development is odd001..017, 9 recordings/500 notes; even8 were not reused. BabySlakh uses existing development tracks 1,2,3,4,5,7,8,9,10,11,12,13,14,16,19, 7,606 reference notes. Its four historical test tracks were previously evaluated and are not a fresh holdout; this campaign did not score or select on them. Thirteen development tracks have one bass source, two have two sources. A single bass source does not itself establish monophony. Bass references merge the catalogued bass instruments; string/fret correctness has no human fingering truth.

IDMT audio/annotations/MIDI stay private for noncommercial internal evaluation: official CC BY-NC-ND4 and bundled PDF CC BY-SA4 disagree. BabySlakh's existing catalog identifies CC BY4; media and annotations also remain private here. Neither dataset's individual events are included in this report.

Existing main has uncommitted transcription/cache/tuner changes. Only read-only private source snapshots were used for the two already-existing decoder configurations; those user changes are not proposed in this PR. CPU ONNX IDMT inference is fresh and shared across decoders. BabySlakh replays historical GPU activations, with exact input audio/model hashes checked across30 routes and39 cache metadata entries. It is not fresh separation/inference. No runtime comparisons across providers are claimed. Existing source changes and809 baseline file hashes were preserved;27 previous synthetic replays match.

## R1: stage attribution before choosing a correction

Frozen plan: compare native notes, performance MIDI roundtrip, fixed1/48-quarter grid and GP5/MusicXML readback, including a reference-oracle notation route. Fixed metrics:50ms onset,50cent pitch, offset max(50ms,20% reference duration),10ms frames. Micro scores below sum matches/estimated/reference counts; full stage aggregates include failures in [CSV](SYSTEM_AUDIT_20261023.csv). These are development findings, not independent test accuracy. BabySlakh numbers are provided-MIDI label scores; R4 below finds unresolved acoustic pitch alignment, so they must not be interpreted as verified sounding-pitch accuracy.

| Input / fixed decoder | Precision | Recall | Onset F1 | With PR8 F1 | Offset F1 before/PR8 |
|---|---:|---:|---:|---:|---:|
| IDMT direct / default | .3867 | .7540 | .5112 | .6180 | .3078/.3721 |
| IDMT direct / July | .2860 | .7420 | .4129 | .5266 | .1603/.2044 |
| Baby true bass / default | .2515 | .3789 | .3023 | .0895 | .1108/.0353 |
| Baby cached separator / default | .0928 | .0956 | .0941 | .0442 | .0250/.0114 |
| Baby true bass / July | .3459 | .6883 | .4604 | .1964 | .1493/.0469 |
| Baby cached separator / July | .2902 | .4351 | .3482 | .1698 | .1046/.0495 |

PR8 does not establish safe general use against the supplied labels: Baby degradation is also present among the13 one-bass-source tracks (default truebass F1 .3642→.1085; separated .1041→.0482), not just the two multi-bass tracks. Keep it optional and report its domain failure. No new activation thresholds were tuned on IDMT. July settings transfer poorly to IDMT; decoder settings and harmonic suppression must not be chosen from these results as a universal preset.

With default decoding, cached-separated Baby output misses6,879/7,606 notes versus4,724 with true bass. With July, misses4,297 versus2,371, alongside8,093/9,901 extra notes. These label-based omissions warrant separation and note-detection diagnosis; R4 prevents attributing them solely to acoustic detector errors. Offset-only work cannot resolve the octave-alignment uncertainty. Historical cache mono16k SI-SDR median is3.926dB versus rawmix -6.183dB, aligned without delay search; original separator CLI parameters were not recorded, so this is cache characterization, not a reproducible fresh separator benchmark.

The initial readback audit was wrong: PyGuitarPro tie `realValue` can resolve a later string value, and music21 cross-voice stripTies can join unrelated events. Original results are preserved; corrected explicit cursor/voice/string readback produces198 chains:197 readable,196 exactly preserving the quantized target. One reference-oracle chain refuses a note shorter than the supported grid. One XML chain merges two adjacent same-pitch attacks into one explicit tie; this is a real existing exporter loss. Do not patch the reader to conceal that tie. Performance MIDI can also lose overlapping same-pitch noteoffs before export. These losses are distinct from acoustic inference.

The120BPM performance container preserves seconds but is not a recovered musical beat grid. Fixed-grid/reference-oracle integrity is not evidence of correct musical rhythm or fingering.

## R2: independently corroborated octave replacement

Hypothesis fixed before evaluation: replace only an existing note's octave when a trimmed pYIN core has at least3 frames,90% consensus, voiced probability>=.9, and the BasicPitch target activation median is>=current and>=.3. Retain timing/velocity, abstain on conflicting overlaps. New13 synthetic cases independently cover low/high pitch, weak fundamental, reattacks, rests, short notes, legato, decay, vibrato, silence and unsupported polyphony; then evaluate odd9 only. Adoption requires correct controls without damaging reattacks/rests and an actual note-quality gain without recall loss. No post-result threshold search.

Result:zero actions on all controls and IDMT odd9;720 notes/377 matches remain unchanged.718/720 IDMT notes lack sufficient core support;2 fail the remaining consensus conditions. Rejected as ineffective, not evidence that octave correction is impossible. Reproduction code, frozen plans and private generated controls are saved in the isolated octave-consensus worktree. An initial duplicate-key driver error ran no scoring; its completed input inference was hash-checked and reused by the corrected run rather than duplicated.

## R3: rhythm and separator alternatives

Frozen audio-beat proposal: librosa onset_strength(sr22050,hop512), beat_track(start_bpm120,tightness100,trim=False). Compare against quarter beats from Baby development MIDI tempo maps after5s, crop at last MIDI event; no annotation-driven half/double correction. Adoption requires median CMLt>=.8, every track>=.6 and static-tempo Acc1 within4%>=.9 before rewriting any notes.

Median CMLt rises .3401→.9759, static tempo Acc1 2/10→9/10. But CMLt is0 on tracks8,11,12, .029 on9 and .561 on13. Half/double ambiguity and phase failures violate the per-track criterion. Rejected as an automatic retiming mode. Strong median scores must not hide failed tracks; a user-confirmed tempo/beat proposal is a possible later workflow, not implemented here.

Fresh local Demucs standard get_model('955717e8',repo=existing checkpoint directory) fails at PyTorch2.9 weights_only safe load: unsupported global demucs.htdemucs.HTDemucs. No inference started, remote download, allowlist, weights_only=False, GPU setting or security change was made. Cached comparisons continued. Fresh separator comparisons require an approved compatible loading path; no hidden bypass is proposed.

## R4: verify the reference before explaining the regression

After finding unusually high bass annotation ranges, a new frozen observational plan checked development tracks9,10,16,19 only: first5 nonoverlapping notes with duration>=.3s per track, centered clips of min(.3s,60% note duration), pYIN B0..C6 sr22050 frame2048hop256 plus FFT energy bands at MIDI pitch and +/-12. No annotation shifts or score choices are allowed from this probe. Protocol deviation: the original plan text says .15s, while executed code uses min(.3s,60% duration); the initial plan is preserved and this is recorded as a window-length discrepancy, not silently interpreted as a half-window. Treat R4 as exploratory observation, not a fully protocol-conforming validation. Track9 had no eligible notes; the other three supply8 clips. All8 return F0 about11.9..12 semitones below the supplied MIDI label, with measurable energy at that lower band. This supports a pitch-alignment concern, not proof of renderer transposition: pYIN itself can choose subharmonics and the sample is small.

Independent file/preset review confirms correct same-name MIDI/stem mapping and program33 for the four tracks. Metadata records Scarbee/Pop bass Kontakt presets, but contains no tuning/transpose settings or rendering log. It also says audio_rendered:false/midi_saved:false despite existing files; metadata alone cannot certify acoustic pitch. [The creators' generation notes](https://github.com/ethman/slakh-generation/blob/master/MakingSlakh.md) document Kontakt-based synthesis, not a validated transpose value for these tracks.

Thus retain all raw label-based scores without relabeling, but quarantine causal claims that PR8 removed acoustically correct upper notes. These outputs may instead expose label/render octave mismatch; the available evidence does not decide that. A reference-audited sounding-pitch subset or verified preset/render parameters is now a higher-priority requirement than tuning harmonic thresholds. Do not choose a -12 label correction from improved development scores.

An additional XML single-pass export probe preserved two fresh synthetic controls but failed on the observed long score with MusicXMLExportException (complex durations require normalization). It was not adopted; the existing attack-merge defect remains visible in the audit.

## Optional notation audit and validation

```
cd backend
PYTHONPATH=. python -m src.evaluation.benchmark --audio PRIVATE.wav \
  --reference PRIVATE.mid --separators direct \
  --transcribers basic_pitch,basic_pitch_mono,pyin \
  --notation-audit --notation-strings 4 --output-dir NEW_PRIVATE_DIR
PYTHONPATH=. python -m src.evaluation.notation_audit --midi PRIVATE.mid \
  --reference REFERENCE.mid --strings 4 --output-dir NEW_PRIVATE_DIR
```

Audit is off by default. A requested notation failure makes that run fail while retaining upstream transcription metrics. It writes input, quantized and reloaded events privately with separate metrics and SHA256 of the input; output must be new/empty. The reader supports only this exporter's one-track fixed-tempo4/4 GP5/XML subset. It reconstructs tie continuity explicitly. `exact` compares pitch and seconds at7 decimal places. `unmatched_exact_boundaries_*` are boundary-multiset mismatches, not proof of added/lost acoustic notes; ordered pitch equality and maximum time drift distinguish such cases. GP5 integer90 versus MIDI90.00009000009 can flag ~4.17microseconds of drift and is intentionally not hidden.

Independent review confirmed input scope, native aggregates, tie-reader semantics and the real XML attack merge. The aggregate read was initially denied by automatic approval review due possible historical-test inclusion; read-only generation manifests/driver proved dev15+odd9 scope and a single authorized retry completed. No held-out data was accessed. New unit fixtures cover ties across early measures, overlapping voices, reattacks, fractional/integer tempo, separate accuracy/integrity, failures, empty scores and default-off/opt-in behavior. Local verification:177 backend tests pass, including167 evaluation/TAB tests;27 stored synthetic replays match and809 baseline hashes plus original main status/diff remain unchanged. Test counts are engineering validation, not transcription accuracy.

Next evidence priorities: validate Baby sounding-pitch labels before classifying lower supports as false, recording reference overlap/source/range instead of assuming one stem is monophonic; preserve original note identity through XML duration normalization; compare fresh separation only after resolving the safe-loader compatibility block. Existing even8 must not select these changes; a new untouched recording set is needed for any broader generalization claim. No defaults switched or PR merged.

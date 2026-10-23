# pYIN early-offset diagnosis (no adopted change)

This diagnosis uses only IDMT odd tracks 001–017 (9 recordings, 500 reference notes) and ten newly generated synthetic conditions. The eight even recordings were previously evaluated once and were not reopened for analysis, scoring, threshold selection or comparison here. Byte-hash preservation checks are separate. No transcription, assembly default, raw F0, raw MIDI, note boundary, Web worker or TAB behavior is changed. The local RMS-rise gate remains an offline rejected hypothesis.

## Result and decision

Baseline has 991 estimates: precision 0.389506, recall 0.772, onset F1 0.517773 and onset+offset F1 0.344735. The fixed gate has 865 estimates: precision 0.433526, recall 0.750, onset F1 0.549451 and onset+offset F1 0.331136. Extras fall 605→490, but misses rise 114→125. It loses eleven baseline reference matches and rescues none. On the common 375 reference matches, offset MAE worsens 113.332→116.055 ms (signed mean -94.899→-72.150 ms). Fewer fragments and less-negative average error do not establish improvement. The gate is rejected; no alternative threshold search was performed.

The single hypothesis admits an independent onset split only when mean RMS rises between fixed symmetric 50 ms windows. References do not enter that gate. Falling amplitude can represent either decay or a real softer repick; rejecting both cannot safely distinguish them. No uniform note extension is proposed.

## What the boundaries show

Among baseline onset+pitch matches, 181 estimates end more than 50 ms before the reference. Their boundaries coincide geometrically with 127 independent-onset splits, 42 unvoiced gaps and 12 pitch transitions. This is not complete physical causal attribution. In 107 of the 127 split cases, the same-pitch raw run reaches within 50 ms of the reference offset; the other 20, plus 42 unvoiced and 12 pitch-transition cases, still stop early at the raw level. Raw availability identifies an assembly opportunity, not proof that every onset split is false. All 181 boundary frames pass the activity floor, so low activity is not the observed real-recording boundary explanation in this set.

Removing all independent onsets reduces recall to 0.502 and onset+offset F1 to 0.285455. Removing activity barely changes the odd-recording result. Allowing one-frame pitch runs increases extras to 751. These are diagnostic ablations, not candidates. See the development and cause CSV files. Strata report reference-conditioned recall and signed onset/offset errors by playing expression, excitation and note duration; unmatched extras cannot be assigned a trustworthy reference style, so no style precision is invented.

## New synthetic counterexamples

Ten fixed cases cover 70/25 ms rests, short notes, equal and quieter same-pitch repicks, legato pitch changes, weak decay, near-floor decay, decaying vibrato and silence. Baseline and RMS gate produce identical note events in all ten; this diagnoses residual limits and does not validate the gate. Quieter repicks retain missed onset / pitch-fragment failure, and legato transitions retain extra pitch fragments. Silence metrics are undefined, not successful nonzero accuracy.

Near-floor decay retains voiced correct-pitch raw F0 past the nominal reference end, yet the activity floor ends assembly about 206 ms early. This separates an assembly floor from a missing-F0 limit. The nominal offset is the generator envelope endpoint; very quiet PCM16 tails do not establish a perceptually audible offset. This synthetic mechanism must not be generalized to the real 181 all-active boundaries.

An additional activity-disabled diagnostic improves near-floor onset+offset F1 from 0 to 0.667 but adds an extra note. It degrades rests, short notes and repicks. The estimated-note union covers the entire true rest (69.977 ms and 24.989 ms), versus 11.927 ms and 7.937 ms with normal assembly. Thus extending through existing voiced F0 also smears silence; activity removal is rejected. Centered pYIN frames (4096 samples at 22050 Hz, hop 256) can contain evidence beyond audible boundaries. Preserving raw support alone is insufficient.

## Scope, metrics and reproduction

The evaluation worktree starts at PR6 c34757e359f9393ddfdfc98662f50340624a985c, with only PR7 5023eeac20514a1f36ac7fb4e32024ee90bf9218's metrics and two test files overlaid. No merge is requested. Existing CPU Python 3.11, librosa 0.11.0, numpy 1.26.4, mir_eval 0.8.2 and installed pYIN are used; no new dependencies, models or paid services. Basic Pitch is outside this independent pYIN diagnosis.

Metrics use onset 50 ms, pitch 50 cents, offset max(50 ms, 20% reference duration), with estimate-minus-reference signed errors. Real aggregates are micro note metrics; timing errors condition on onset+pitch matching and the paired comparison holds reference identities fixed. Synthetic frame F1, overlap, extras and misses are retained in CSV; assembly replay runtime is zero and is not an inference speed benchmark. Reference definitions remain the previously validated 948 archive events rather than the paper's unexplained 941 total. Internal noncommercial use only: official CC BY-NC-ND 4.0 and bundled PDF CC BY-SA 4.0 differ. Audio, annotations, reference MIDI and generated audio stay in ignored private results.

From backend, with an existing environment and a fresh ignored output path:

```sh
CUDA_VISIBLE_DEVICES='' PYTHONPATH=. /path/to/existing/venv/bin/python scripts/pyin_offset_diagnostics.py --output-dir benchmark_results/new-private-offset-diagnostics
```

This freezes the synthetic plan, extracts frames once per case and preserves raw files for both assemblies. Odd-recording diagnosis reuses saved frames and the actual resampled audio duration, then roundtrips through MIDI: all 991 baseline events reproduce exactly. Existing raw files are never rewritten. No even-track inference is run. The original main checkout and baseline are preserved. The diagnostic and supplemental rest ablation received independent review with no blocking finding; the reviewer reproduced saved MIDI and raw-hash invariance without reading even data.

## Verification

All evaluation unit modules plus TAB roundtrip: 145 passed (three existing audio-reader deprecation warnings). Saved-frame replay of all 27 older synthetic cases reproduces their baseline events exactly after MIDI roundtrip; this is assembly regression, not fresh model inference. All 809 baseline manifest hashes match, and the original main status and tracked-diff hashes remain unchanged.

For the supplemental no-activity ablation, replay each saved `pyin_frames.npz` with the same F0, voiced flags, times, original onset times and audio duration, changing only `active` to `numpy.ones_like(active)`. Roundtrip through the same precise MIDI writer before metrics. Rest coverage is the union of estimated intervals intersected with the known synthetic reference gap, avoiding double-counted overlaps. This is a counterfactual diagnosis, not a selectable backend.

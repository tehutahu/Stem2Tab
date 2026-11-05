# 終了補正と冒頭継続音補完の事前固定・別評価

このCLIは保存済みcanonical JSONを読み、補正も推論も実行しない。
`metrics.py` の指標とmir_evalの一対一音高/onset対応を再利用する。
音源、モデル、独立確認済み実曲正解はクラウドへ追加していない。IDMTの全17録音は既使用であり、
独立評価に数えない。未使用対象は未確定で、RWC注釈の独立正解としての確認も不足している。
既使用17録音の実IDと別の未使用対象、正解・同期情報を確認するまで実データ評価を開始しない。
試聴用の音量減衰は音符補正ではなく、この検証の候補に混ぜない。

## ローカル担当向けの実行順

1. 未使用録音のID・区間・独立正解の出典/確認方法・共通時間軸と、17件以上の既使用IDを
   確認する。候補パラメーターと下記の採用条件案も、結果を見る前に決める。
   対象選択は録音単位。短区間を同じ録音から多数選んでも独立録音数は増えない。
2. 生のbaselineと正解のcanonical JSON、候補実装ファイルのSHAを用意する。
   既存 `src.evaluation.io.read_midi` / `write_note_events_json` を再利用できる。
   正解はbass-onlyで独立確認済みのものを使い、推定出力を正解へコピーしない。
   baseline収集が必要なら、事前選択を変えずに収集し、結果を見てパラメーターを調整しない。
3. `offset_only` と `prefix_only` の2計画を作り、freezeとcheck-pairを実行する。
   同じ原baseline・正解・区間・許容値からそれぞれの候補を作る。直列適用しない。
4. 候補処理の完了後に、その出力SHAと失敗/未実施をattemptsへ記録しevaluateする。
   出力は新しいファイル名にする。再試行や不都合な区間の除外を黙って行わない。

backendの既存Python環境で実行する。追加インストール・GPUは不要。

```bash
python -m src.evaluation.correction_audit schema plan > plan-schema.json
python -m src.evaluation.correction_audit schema attempts > attempts-schema.json
python -m src.evaluation.correction_audit freeze --plan offset-plan.json --out offset-lock.json
python -m src.evaluation.correction_audit freeze --plan prefix-plan.json --out prefix-lock.json
python -m src.evaluation.correction_audit check-pair --offset-lock offset-lock.json --prefix-lock prefix-lock.json
python -m src.evaluation.correction_audit evaluate --lock offset-lock.json --attempts offset-attempts.json --out offset-result.json
python -m src.evaluation.correction_audit evaluate --lock prefix-lock.json --attempts prefix-attempts.json --out prefix-result.json
```

CLIが正常終了しても採用とは限らない。JSONの `decision` と `hold_reasons` を読む。
計画/attemptsの不整合や上書き要求はCLIを非ゼロ終了させ、精度結果を作らない。
その場合は入力とstderrを保存する。予定された候補の実行失敗とは区別する。
出力は新規作成だけを許し、既存ファイルを上書きしない。入力を変更して補正をやり直す場合も
別計画/別結果へ保存する。計画変更後の同じ対象は調整済みとして扱い、独立評価を名乗らない。

## 計画の雛形

以下は意図的に未完成。実ID/実SHA/実検証根拠に置換するまでfreezeできない。
`known_reused_recording_ids` は既使用IDMT全17件と追加の既使用対象をすべて列挙する。ダミーIDで埋めない。
未使用IDは `held_out_declared_unused`、既使用は `development_reused`。
コードが判定できるのは宣言間の整合性であり、独立性や正解の内容の自動認証ではない。

```json
{
  "dataset_kind": "local_recordings",
  "known_reused_recording_ids": ["FILL_ALL_17_OR_MORE_ACTUAL_IDS"],
  "baseline_pipeline_identity": "FILL_CODE_MODEL_CONFIG_IDENTITIES",
  "candidate": {
    "id": "offset-candidate-v1", "kind": "offset_only",
    "implementation_files": [{"path": "candidate.py", "sha256": "FILL_SHA256"}],
    "parameters": {"FILL_ALL_REAL_PARAMETERS": true}
  },
  "metrics": {
    "onset_tolerance_ms": 50, "pitch_tolerance_cents": 50,
    "offset_ratio": 0.2, "offset_min_tolerance_ms": 50, "frame_hop_ms": 10
  },
  "prefix_seconds": 0.25,
  "criteria": {
    "minimum_held_out_recordings": 3,
    "minimum_improved_held_out_recordings": 2,
    "minimum_primary_recoveries": 1,
    "maximum_broken_correct_offsets": 0, "maximum_worsened_offsets": 0,
    "maximum_newly_missed_onsets": 0, "maximum_extra_increase": 0,
    "maximum_octave_increase": 0, "maximum_bad_recovered_continuing_offsets": 0
  },
  "recordings": [{
    "id": "FILL_ACTUAL_UNUSED_RECORDING_ID", "usage": "held_out_declared_unused",
    "usage_evidence": "FILL_LOCAL_UNUSED_CONFIRMATION",
    "targets": [{
      "id": "clip-1", "start_seconds": 10, "end_seconds": 20,
      "alignment": "FILL_CONFIRMED_SOURCE_TIME_MAPPING",
      "baseline": {"path": "raw-baseline.json", "sha256": "FILL_SHA256"},
      "reference": {
        "status": "verified",
        "artifact": {"path": "independent-reference.json", "sha256": "FILL_SHA256"},
        "provenance": "FILL_REFERENCE_SOURCE_AND_ORIGINAL_HASH",
        "verification": "FILL_INDEPENDENT_VERIFICATION_METHOD"
      }
    }]
  }]
}
```

数値は採用条件の**提案**であり、実曲で達成済みの基準でも統計的に妥当と確認済みの基準でもない。
小規模な候補スクリーニングの暫定条件であり、一般化や全曲自動採用の証拠にはしない。
対象不足なら試験不能として残し、既使用対象を混ぜて必要録音数を満たさない。
0.25秒の冒頭幅も固定候補で、実測から選んだ値ではない。計画と条件は評価前に合意する。
全設定はlock内へ展開され、計画全体・入力・コードをハッシュで固定する。freeze時刻と
ライブラリ版も記録するが、外部の時刻認証や、過去に人が結果を見ていない証明ではない。
実装ファイルは関連する補正モジュールもすべて列挙する。attemptsのパラメーター一致は
外部処理がその値を使ったことの証明ではないので、実行コマンド/ログを別途保持する。

## 候補ごとの禁止変更

- `offset_only`: 元の `(開始, 音高, velocity)` の集合と重複数を維持し、終了だけを変更可能。
- `prefix_only`: 元の全音符を終了も含めてそのまま残し、区間先頭にのみ新音符を追加可能。
- いずれも同じ原baselineへ適用する。check-pairはbaseline/対象/基準の不一致や直列適用を拒否する。
- 原JSON/MIDIは読み取り専用で保持し、比較用の新しいcanonical JSONを別名で保存する。
  不正な負/ゼロ長はfailedにし、削除や丸めで修復しない。

## 時間軸と指標の解釈

すべて秒単位の元録音時間軸を使う。baseline/候補は指定区間内、正解は区間より前からの音も
含む元時間軸のままとする。clip-relative出力は、確認済みの一定原点を足した別JSONを用意し、
変換をalignmentへ明記する。時間軸を推測しない。最大30秒・予測500音、参照ファイル20k音/
5MiB。原録音自体の先頭より前の継続が不明なら、正解を捏造して補わない。

- 既存指標は、区間と交差するノートを境界で切ったコピーに計算する。
  冒頭継続音には仮の境界onsetができるため、通常の実アタック検出F1と同じ名称の値を
  無条件で比較しない。`metric_scope` にこの条件を記録する。
- `newly_missed_onsets` は真の開始が区間内にある既正解対応の消失数。
  見逃し/余分は音高とonsetの組合せによる一対一対応であり、オクターブ誤りと重複し得る。
- 終了の遷移診断は、baselineでの同じonset対応と元音符identityを使う。
  正解終了許容は元正解音長に対する `max(50ms, 20%)`。mir_evalと同じ丸め・包含境界を使う。
  正解だった終了の破壊、誤りからの修復、絶対終了誤差の悪化を別々に数える。
  実終了が右区間外なら未採点。重複anchorでidentityが曖昧なら未判定とし保留する。
- `prefix` は冒頭固定幅だけの見逃し/余分と、真の開始が区間前にある継続音の回復/消失を示す。
  新規補完音の終了誤り、終了が採点不能の場合も分ける。継続音が無い区間の余分追加も数える。
- 録音ごとに非重複区間のTP/見逃し/余分を合計しmicro F1を計算する。
  区間F1の単純平均ではなく、録音数も区間数と区別する。境界で切った同じ継続音が隣の区間にも
  出る場合は区間観測としての集計であり、録音全体の固有音符数ではない。

## 試行の記録と保留

`attempts` はロックSHA、実際の候補パラメーター、各targetの状態を持つ。
パスはattemptsファイルから相対、baseline/正解/コードは元planファイルから相対解決する。

```json
{
  "plan_sha256": "FILL_LOCK_PLAN_SHA256",
  "candidate_parameters": {"FILL_ALL_REAL_PARAMETERS": true},
  "outcomes": [
    {"target_id": "clip-1", "status": "completed", "artifact": {"path": "candidate.json", "sha256": "FILL_SHA256"}},
    {"target_id": "FILL_OTHER_PLANNED_ID", "status": "failed", "reason": "保存した実際の失敗理由"}
  ]
}
```

全IDは事前計画に必要で、重複や予定外IDは拒否する。未実施は`not_evaluated`と理由を記録。
記録の無い予定対象も未評価として残す。正解が `hypothesis` / `unavailable` なら精度値を作らない。
検証済み無音区間はextraを数えるが、正解音数0の正例F1はnull。failedや未評価を0点や満点で
埋めず、比較欄をnullにする。集計は評価済み部分のみで、網羅不足なら必ずhold。

採用条件案は、未使用3録音以上・そのうち2録音以上で対象の誤りを修復・対象の回復1音以上とし、
全予定録音で正しかった終了の破壊/終了悪化/新しい見逃し/extra増/オクターブ増を0とする。
冒頭候補には継続音の消失、新補完音の終了誤り/採点不能も許さない。
平均F1が上がっても退行があればhold。効果0もbefore/afterをそのまま保存する。
全条件を満たしても `ready_for_human_review` までで、既定方式への昇格は実行しない。
独立性・正解・権利・同期・聴感/実奏の確認と、別途の採用判断が必要である。

結果は `benchmark_results/` 等のローカルignored領域へ保持する。
録音/正解ノート列/モデル/私的な実行結果をGitへ加えず、クラウドへ転送しない。

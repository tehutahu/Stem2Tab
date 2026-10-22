# CPU pYINと音符組立の評価

2026-10-22。既存librosaとCPUのみで、評価用pYINアダプターと参照非依存の音符組立器を実装した。
既定のBasic Pitch、Web worker、依存定義・lockは変更していない。
本結果は合成信号の診断であり、実曲・実ベースの採譜精度を示さない。
信頼できる実曲reference MIDIは引き続き存在しない。

## 実装と保存する情報

- `src/evaluation/pyin.py`: `pyin_raw` と `pyin` をprotocol/registryへ追加。重いimportは実行時のみ。
  pYINはA0〜G4、窓4096sample、hop256、22050Hz、center=Trueで固定。
- `src/evaluation/note_assembly.py`: validated frozen `AssemblyConfig` と純粋な `assemble_notes`。
  2frame以上の安定pitch runを採用し、両隣が同pitchの1frame揺れだけを補正する。
  unvoiced・低活動・無効F0のgapは橋渡ししない。独立onsetは同pitch再ピッキングを分割する。
- 生F0/voiced/probability/time、活動量・onsetは `pyin_frames.npz` に保存。
  未処理ノートは `raw.mid` と `raw_events.json/csv`、組立後は別の `assembled.mid` に保存。
  原データを書き換えず、共通events/Performance MIDI/指標には選択した方式のノートを用いる。
- referenceは合成生成・評価側だけが使い、推論/組立器には渡さない。
  MIDIはresolution22050で保存し、再読込した値を評価の正本にする。

## 検証ループとデータの使い分け

既存11条件（55音）をdevelopmentに使用した。音長0.2/0.35/0.8秒、peak0.1/0.25/0.8、
倍音比、音順の変更を含む。初版v1は10ms中心窓RMSに対して `max(1e-4, global_peak_RMS×.03)` の
活動ゲート、2frame安定性、同pitchのonset分離50msを用いた。

未使用の検証A/Bを最初の推論前に固定した。Aは8条件・25音で、同音再ピッキング、短音、短休符、
弱基音、連続legato、同一音源内の混在強弱、無音を含む。
Aの結果から一般的な2問題を発見したため、Aを開発へ移してv2を実装した。

1. global peakに対するゲートが、大きい音に続く小さい正音を丸ごと消した。
   v2は既存の絶対RMS floor `1e-4` だけを使用し、他の音の音量に判定を依存させない。
2. release付近のonset誤検出が、末尾23msの同pitch片を作った。
   v2は既存50ms条件を分割の前後へ対称に適用し、両側の最低2frame条件も維持する。
   数値の探索はしていないが、これはAの結果を使った設計変更である。
   50ms未満の末尾再ピッキングを抑制する可能性は制限として残る。

v2の推論/組立処理を固定してから、検証B（別の音高・音長・強弱・倍音比・位相を使う8条件・21音）を
初めて実行した。Bの結果による追加調整はしていない。現在はBも評価済みの回帰条件であり、
今後の最適化には別の未使用条件が必要である。
各planに全suite定義・source hash・設定を保存し、v1/v2 source snapshotも評価成果物へ保持した。
モデル推論とraw出力の一致を独立確認し、改善がrawデータの書換えによるものではないことを確認した。

## 改善と退行

F1は全条件の1対1対応数を合算したmicro値。onset±50ms・pitch±50cents、
offsetは参照長20%または50msの大きい方を許容する。
extra/missedはonset+pitch対応に基づき、境界のずれでもextraとmissedが同時に増え得る。
無音はreferenceを指定せず、空referenceからF1を作らない。

| 条件集合 | 方式 | 推定音数 | extra / missed | onset F1 | onset+offset F1 |
|---|---|---:|---:|---:|---:|
| 開発55音 | Basic Pitch | 115 | 73 / 13 | .494 | .376 |
| 開発55音 | pYIN raw | 165 | 134 / 24 | .282 | .273 |
| 開発55音 | 組立v1 | 71 | 16 / 0 | .873 | .873 |
| 開発55音 | 組立v2 | 55 | 0 / 0 | 1.000 | 1.000 |
| A・25音 | Basic Pitch | 25 | 17 / 17 | .320 | .040 |
| A・25音 | pYIN raw | 56 | 38 / 7 | .444 | .296 |
| A・25音 | 組立v1 | 35 | 14 / 4 | .700 | .667 |
| A・25音 | 組立v2 | 29 | 7 / 3 | .815 | .778 |
| 未使用B・21音 | Basic Pitch | 23 | 17 / 15 | .273 | .045 |
| 未使用B・21音 | pYIN raw | 71 | 53 / 3 | .391 | .326 |
| 未使用B・21音 | 組立v2 | 25 | 7 / 3 | .783 | .739 |

開発55音の1.000は既定許容内で55対55が対応した意味で、時刻がsample単位に正確という意味ではない。
原則として方式採用の判断は開発セットのスコアでは行わない。

再ピッキングの実測では、Aの休符付き5音・休符なし3音、Bの密な同音3回がv2で別noteとして対応した。
Bの同音3回はonset+offsetとも1対1で3対3が一致した。末尾60〜90msの再発音も単体テストで保持を確認。
v1で消えたAの小音量E1はv2で戻り、Bの別の混在強弱も3対3で対応した。
境界短片を全て結合する方式ではなく、gapと独立onsetを保持した結果である。

悪化・未解決例も残る。

- Aの75〜110ms短音：raw onset F1 `.444` → 組立v2 `.333`。
  1frameの正しいpitch候補も安定性規則で落ちる。E1自体がF0へ出ない例もあり、全てを組立だけでは回復できない。
- Aの弱基音：rawのF1 `1.000` に対しv1は余計な分割で `.750`。v2で `1.000` に戻った。
- Bのlegato：Basic Pitch `.667`、raw `.364`、組立v2 `.444`。
  組立後も4extra/1missedが残り、onset+offset F1は `.222`。
  28→29→33の遷移で30/31/32の短片が出て、33開始は参照より約56ms遅い。
- B全体で3missed/7extraが残る。短音・legato・長短混在が対象で、無音負例は全方式0音だった。

同pitch反復は1対1のonset/onset+offset指標と実イベント列で判定する。
`note_diagnostics.json` の時間重複候補は、同じ推定持続音が複数参照を覆い得るため、
それだけで反復の保持や分割の成否を判断しない。
B0の候補被覆だけでなく、全音の偽陽性・境界・反復のトレードオフを評価した。

## 処理時間・残る限界

検証Bの8条件合計のbenchmark内時間は、Basic Pitch8.30秒、pYIN raw2.77秒、組立v2 2.52秒。
import・入力/MIDI/preview IOを含み、同プロセスでBasic Pitch→raw→組立の順に実行する。
rawと組立で同じpYINを再計算し、配列が一致することを確認した。
JIT cacheや初回importの条件が異なる単発計測なので、モデル単体の速度優劣は主張しない。
各条件の処理時間も集約CSVに残した。

窓4096sample（約186ms）に対する65〜110ms短音、legatoのF0遷移は依然として難しい。
参照に合わせて未検出音を作る・一律に境界を移動する・同pitchを全結合する修正は行わない。
v2後のBで失敗を確認した段階で本反復を止め、Bへ追加最適化して良い結果だけを選ぶことを避けた。
RMS floorへの変更後の背景ノイズ耐性、スラップ・実際の減衰・奏法の違いは未評価である。

次の実装候補は、短音/legato向けの複数時間分解能F0と、独立onset・pitch遷移の対応付けを
別の未使用条件で比較すること。実用上の改善を判断するには、許諾済みのユーザー提供短区間で、
同音再ピッキング/legato/弱音を含む実ベースを耳で比較する必要がある。
正解依存指標は、その音源の信頼できるbass-only MIDIが用意できた場合だけ計算する。

## 再現とレビュー

```bash
cd backend
uv run pytest tests/unit/test_evaluation_*.py -q
CUDA_VISIBLE_DEVICES='' BASIC_PITCH_MODEL_SERIALIZATION=onnx \
  uv run python -m src.evaluation.synthetic --suite development --output-dir benchmark_results/dev-v2
CUDA_VISIBLE_DEVICES='' BASIC_PITCH_MODEL_SERIALIZATION=onnx \
  uv run python -m src.evaluation.synthetic --suite validation_a --output-dir benchmark_results/a-v2
CUDA_VISIBLE_DEVICES='' BASIC_PITCH_MODEL_SERIALIZATION=onnx \
  uv run python -m src.evaluation.synthetic --suite validation_b --output-dir benchmark_results/b-v2
```

出力先は新規/空に限る。既存依存のpYINだけなら `--transcribers pyin_raw,pyin` とし、
Basic Pitchが未導入でも集約できる。モデルや公開datasetは取得しない。
入力WAV・生成reference・raw MIDI・NPZ等はgitignoreされた `benchmark_results/` に保存し、コミットしない。
各runのmanifest、全体plan/provenanceに設定・source/input/output hashを残す。

[全138結果のCSV](pyin_assembly_results.csv)と[v1→v2の推論処理差分](pyin_v1_to_v2.patch)を同梱した。
v1を再現する場合は作業を分離したcheckoutでこの差分を逆適用して同じsuiteを実行する。
数値を作り直す時は別の出力先を使い、既存結果を上書きしない。
独立レビューで入力/予約定義・全指標再算出・raw保存・生F0からのnote復元・反復音の1対1対応を確認した。

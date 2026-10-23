# 実ベース録音の任意倍音重複フィルタ評価

2026-10-23。既定Basic PitchとWeb workerを変更せず、評価CLIに任意の `basic_pitch_mono` を追加した。
単音ベース向けの限定的な後処理であり、本番採用・TAB運指精度・複音への一般化は主張しない。

## 原因と変更

奇数9曲500音だけを開発に使用。Basic Pitchの余剰598音中427音は、参照と重なり上位倍音/オクターブ関係にある候補だった。
これは注釈と既定の対応規則による分類であり、音響的原因の確定ではない。pYIN組立の早切り181件中127件は独立onsetでの分割だった。
今回は最も件数の大きいBasic Pitchの倍音重複だけを対象とし、pYINの規則やモデルは変えていない。

保持済み低音noteと開始差50ms以内、時間区間が厳密に包含され、2〜8倍音から50cents以内の上位noteを抑制する。
設定は独立したfrozenモデルで、MetricConfigや参照を推論へ渡さない。数値探索は行っていない。
抑制された中間noteは別noteの抑制根拠に使わず、残るnoteの時刻・音高・velocityは一切変更しない。
生 `raw.mid`、rawイベントJSON/CSV、別の `filtered.mid`、採否理由JSONを保存する。

## 分割と検証

IDMT-SMT-BASS-SINGLE-TRACKSの奇数9曲500音を改善用、偶数8曲448音を候補評価用としてbaseline前に固定した。
偶数はbaselineとして既に評価済みである。候補の開発・選択中は再参照せず、実装・設定・source hash固定と独立レビュー後に候補を一度だけ測定した。
偶数結果を使った候補選択・追加調整・再評価は行っていない。今後の調整には新たな未使用データが必要になる。
全17曲で候補raw MIDIのbyte hashとrawイベントがbaselineに完全一致し、候補イベントが厳密な部分集合であることを確認した。

指標はonset±50ms/pitch±50cents、offset=max(50ms,参照長20%)。F1/P/Rはトラック内の1対1対応数を合算したmicro値。
frame F1は曲別値のmacro平均。符号付き時刻誤差はonset+pitchで対応した音のみを対象とする。

| split | 方式 | precision | recall | onset F1 | onset+offset F1 | extra/missed |
|---|---|---:|---:|---:|---:|---:|
| development | basic_pitch | 0.387 | 0.754 | 0.511 | 0.308 | 598/123 |
| development | pyin | 0.390 | 0.772 | 0.518 | 0.345 | 605/114 |
| development | basic_pitch_mono | 0.524 | 0.754 | 0.618 | 0.372 | 343/123 |
| evaluation | basic_pitch | 0.431 | 0.886 | 0.580 | 0.345 | 525/51 |
| evaluation | pyin | 0.444 | 0.871 | 0.588 | 0.382 | 488/58 |
| evaluation | basic_pitch_mono | 0.591 | 0.886 | 0.709 | 0.421 | 275/51 |

改善側ではbaseline偽陽性255音を削除し、既存対応音の削除は0音。
固定後の偶数側では偽陽性250音、既存対応音0音を削除した。
recallの悪化があればprecision改善と分けて評価し、余剰音の減少だけで総合改善としない。

[全体・符号付き時刻誤差・処理時間](idmt_harmonic_results.csv)、[音長/奏法別の参照recallと時刻誤差](idmt_harmonic_strata.csv)を保存した。
奏法別集計は参照音に対するrecallであり、余剰音に真の奏法ラベルを与えてprecisionを作っていない。

## 回帰・制限

独立レビューでraw保全、subset、固定設定、抑制連鎖禁止、GT非依存、奇数だけのFP/TP削除を確認。全評価unit+TAB回帰152件成功。
既存合成27条件(11+8+8)を4方式で実行し、候補vsBasic Pitchのケース別onset F1・offset F1・recall退行は0。
合成A/Bは過去に使用済みの回帰条件であり、新しいholdoutではない。[合成集計](idmt_harmonic_synthetic_regression.csv)。
これは単音の包含判定である。偽の低音subharmonicが残ると本物の上位音を削除し得る。単体テストにもこの反例を明示した。
pYINの過分割・早いoffset、Basic Pitchの未検出音、legato・dead note・harmonicsの曖昧さは未解決。
処理時間はimport/cache/IOを含む参考値で、baselineと候補の実行順も異なるため速度優位は主張しない。

## 入力・再現

[公式案内](https://www.idmt.fraunhofer.de/en/publications/datasets/bass_lines.html)、[配布record](https://zenodo.org/records/7544099)。
ZIP MD5 `c85540c664e8b13badd86880bacf11fa`、20,499,169 bytes。
現行公式CC BY-NC-ND 4.0と同梱PDF CC BY-SA 4.0の表記差を保持し、承認済みの非商用内部評価のみで使用した。
XML/CSVの948音は全件一致。[論文](https://dafx.de/paper-archive/2013/papers/24.dafx2013_submission_9.pdf)Table 2は941音。原因は未確定で、941へ合わせる除外はしていない。
秒単位のXML時刻とMIDIの高精度roundtripを検証。4音のoffsetがWAV末尾を最大7.431ms越えるが、切詰めず保持した。
注釈弦番号は1=E/2=A/3=D/4=Gであり、TAB側の番号へそのまま流用しない。
Python3.11.14 / BasicPitch0.4.0 / librosa0.11.0 / numpy1.26.4 / ONNXRuntime1.23.2 / mir_eval0.8.2、CPUのみ。追加依存・モデル・料金なし。
同梱ONNX SHA256 `2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec`。

```bash
cd backend
CUDA_VISIBLE_DEVICES='' BASIC_PITCH_MODEL_SERIALIZATION=onnx PYTHONPATH=. /path/to/existing/venv/bin/python -m src.evaluation.benchmark --audio /private/audio.wav --reference /private/validated-reference.mid --separators direct --transcribers basic_pitch,basic_pitch_mono,pyin --output-dir benchmark_results/new-private-run
```

出力先は新規/空に限る。音源・XML・CSV注釈・参照MIDI・加工音源・NPZ・音符単位診断は全て非公開のignored出力に保持し、このレポートは集計のみを含む。
PR6の固定headにPR7の指標修正3ファイルを重ねた評価であり、両PRをマージしたものではない。

固定記録SHA256 `95ab63f48f7378e1fff92ec2856be9f9cef078cba3e2323d513fb03943913741`。音符境界を変更していないため、候補のoffset F1上昇は余剰削減によるもので、境界精度自体の改善を意味しない。

追加調査: [偽subharmonicと非採用guardの反例](SUBHARMONIC_RISK.md)。production方式は変更せず、評価済み偶数側を次候補へ再利用していない。

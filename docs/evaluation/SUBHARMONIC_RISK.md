# 偽subharmonicによる正解上位音削除リスクの調査

2026-10-23。PR8のproductionフィルタを変更せず、最低support音高をE1(MIDI28)へ制限する一つのoffline仮説を調べた。**採用しない。**
奇数9曲では効果がなく、音域内の偽低音を根拠にした誤削除を防げないためである。既定backend・raw MIDI・生存音符の境界・pYIN offsetは変更していない。

## 範囲と分離

- 今回の実録音分析は改善用奇数9曲500音だけ。評価済み偶数8曲の音源・注釈・スコアを次候補の選択・閾値調整・反復比較に使っていない。偶数候補を再実行していない。
- support>=28は標準4弦ベースのE1という意味から事前に一値を固定し、数値探索していない。診断script内だけの仮説で、adapterには登録しない。
- 新規合成音声8条件のモデル実出力と、6つの人工raw note反例は別集計。人工反例を「Basic Pitchが実際に出したsubharmonic」と解釈しない。
- 初期の一般filter用MIDI16反例はBasic Pitchのモデル音域外なので、公開集計や採否根拠に含めていない。再現CLIは音域内のB0(23)/B1(35)を使う。

## 改善側の実測

| 方式 | 推定音数 | precision | recall | onset F1 | extra/missed |
|---|---:|---:|---:|---:|---:|
| Basic Pitch raw | 975 | .3867 | .754 | .5112 | 598/123 |
| PR8任意フィルタ | 720 | .5236 | .754 | .6180 | 343/123 |
| offline support>=28 | 720 | .5236 | .754 | .6180 | 343/123 |

PR8が削除した255音はすべてE1以上の支持低音による。support>=28は全9曲でPR8と同一のイベント列となり、新たなFP削除・TP救済は0だった。
したがって、音長/奏法別recall・対応音の符号付きonset/offset誤差もPR8から変わらない。音符境界の変更による改善はない。[改善側集計](subharmonic_risk_development.csv)。

## 新しい合成音声とモデル実出力

既存の合成development/A/Bとは別の8条件を、推論前に固定した。E2、A2、弱基音E1、5弦B0、音量差を持つE2再発音、E1→E2遷移、高いG4、無音を含む。
異なる位相・長さ・振幅・倍音比を用い、正解時刻をsampleから作って高精度MIDI化した。既存Basic Pitch ONNXをCPUで実推論し、rawとPR8のモデル出力byte一致を確認した。
この8条件ではPR8/guardとも削除0で、3方式の推定イベントが一致した。7非無音条件9音に対するmatchedは6音、推定15音、micro precision=.4000、recall=.6667、onset F1=.5000。
無音は全方式0音で、正解が空のF1を定義していない。この小集合は誤削除を自然発生させた証拠を与えず、安全性の保証にもならない。[モデル実出力集計](subharmonic_risk_audio.csv)。

## 人工raw反例と識別不能性

同じ時刻・長さ・velocityを持つMIDI28と40があると、(a)28が正解で40が倍音FP、(b)28が偽低音で40が正解、の二つの真値が可能である。
raw note入力が同一なので、note-onlyの決定則は真値を区別できない。単音という仮定でも正しいoctaveは特定できない。
PR8とsupport>=28は(a)のrecall=1、(b)のrecall=0。MIDI28はguardの音域内なので、guardはこの問題を防げない。
厳密な同時刻や強い低音velocityも反例に含めており、これらの属性だけで正しさは保証されない。

別の同一入力23/35では、35が正解ならguardはrecallを0→1へ救済する一方、23が正解ならprecisionを1→.5へ落とす。
これは4弦用の音域仮定を5弦の真の低音へ適用する代償であり、改善の保証ではない。28/47の第3倍音関係でもguardは正解上位音を落とす。
[6反例×3方式の集計](subharmonic_risk_injected.csv)。これはモデル実推論の統計に混ぜない。

## 再現と回帰

```bash
cd backend
PYTHONPATH=. /path/to/existing/venv/bin/python scripts/subharmonic_counterexamples.py --output-dir benchmark_results/new-private-counterexamples
```

出力先は新規に限る。この診断CLIはネットワーク・モデル推論・実録音・元注釈を使わない。真値は指標計算側だけに渡し、filterは観測noteだけを受け取る。
独立レビューで識別不能性、非採用guardの限界、モデル実出力とfault injectionの分離を確認した。
新規4意味テストを含む全評価/TAB回帰156件成功。productionソースが不変なので、既存合成27条件は保存rawからfilterを再生し、前回PR8のイベントと全件一致を確認した（モデル再推論ではない）。
別途、新規音声8条件×2モデル経路の実推論は全件成功した。依存・モデル・ドライバの追加や料金はなく、音源・注釈・参照MIDI・加工音源・音符単位診断は非公開のignored出力に保存した。

PR8の調査前head `5cabd728b2e5fd8382d1ad20c9159fe280fef9f4` の [GitHub CI run 39890153757](https://github.com/tehutahu/Stem2Tab/actions/runs/39890153757) はbackend/frontendともcompleted/success。
失敗がなかったためCI設定変更や再実行はしていない。今回の追記は診断コード・テスト・集計・説明だけであり、PR6/7/8をマージしていない。

結論は「誤削除防止条件を得た」ではなく、「note-onlyの一条件では保証できず、効果を示せなかったため非採用」である。pYINの早いoffsetは別原因として未修正のまま残す。

production filter SHA256 `5cc10bbd0e8ee5e326f67d3adbb7261c1019499d2c192dc4be3b351976d4d4a5`。新規合成計画SHA256 `05a5e0ef5400ecb5aac46a69e00f86c24b740a026fd22bfd5f0166ab04a959ad`。

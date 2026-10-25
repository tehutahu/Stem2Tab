# FCPE 固定合成評価: 現設定を不採用

2026-10-25 の固定比較で FCPE の現設定を不採用とし、探索を終了した。
既定コード、Web worker、依存定義は変更しない。これは合成音だけの診断であり、
実ベース音源や未使用 holdout に対する採譜性能の主張ではない。

## 保存した記録

- [protocol.json](experiments/fcpe-synthetic-20261025/protocol.json): 推論前に固定した v2 の**元バイト列**。
  SHA-256 は `57c53ebffbc1eb637db13b9c4ac286d30b1bff3972efa84645a024ae1069044e`。
- [results.json](experiments/fcpe-synthetic-20261025/results.json): 集計の分子・分母、判定、資源計測、版・ハッシュ。
  元の環境識別子、認証情報、署名 URL、私的データは含めない。

固定時刻は `2026-10-25T07:36:58.785023+00:00`、推論は同日
`07:38:24.152819`–`07:38:33.301363` UTC。元の隔離 harness・raw 配列・音源・重みは
ローカルに残し、この変更では配布しない。完全な可搬推論 runner を公開したという意味ではない。
プロトコル中の絶対パスは当時のファイル同一性の記録であり、移植先の必須パスではない。

## 固定条件

seed `20261025`、mono PCM16/22050 Hz、46 clips / 62.700045 秒 / 76 labeled events。
40/60/90/120/200 ms の短音、E1/A1 の反復と gap 0/20/50/100 ms、1/2 秒持続音、
C1/E1/A1/D2/G2、sine・倍音 pluck・欠損基音、SNR 20/10/0 dB、純粋ノイズ、無音を含む。
B0 は FCPE の下限 32.7 Hz 未満のため除外。正解は合成時の整数サンプルで固定した。

FCPE は frame F0 のみ。`DDSP_200k.pt`、source commit
`6a149c1afb1c7e7821b71869dfb31ad50c95b516`、local_argmax、confidence **>0.006**、
TTA/無声補間/出力長補間/F0 clipping なし。外部 soxr_hq resample 後の 16000 Hz 入力を使用。
ロードは `torch.load(weights_only=True, map_location='cpu')` と strict state loading。
unsafe loader や allowlist 緩和は使用しなかった。重みは取得済み 43,362,881 bytes、
SHA-256 `8544427eebbf2baef6213cc9a05057e46961617a8e5bd96975a0d42da6a09059`。
公式の固定参照は [FCPE source](https://github.com/CNChTu/FCPE/tree/6a149c1afb1c7e7821b71869dfb31ad50c95b516) と
[checkpoint](https://huggingface.co/ChiTu/FCPE/resolve/aeec888cd3eecd9a350945205a86340ef9293548/DDSP_200k.pt)。
code は MIT、重みは CC BY-NC-SA 4.0。本件は非商用の個人評価で、重みを再配布しない。

Basic Pitch 0.4.0 / ONNX CPU の主比較は raw note probability の最高 bin を選び **>0.3**
で有声とする単音投影。複数 bin の情報を捨てるため、全出力との優劣には一般化できない。
全 active-bin-set の別集計も保存した。既定 note events は onset 0.5、frame 0.3、
最小長 127.7 ms、周波数制限なし、Melodia/inferred onsets/pitch bends 有効で別途生成した。
FCPE に onset 検出や note-event 精度は付与しない。

共通 grid は `0.005 + 0.01*k` 秒、正解区間は半開区間、pitch 許容は 50 cents、boundary collar なし。
native frame の最近傍を採用し、同距離は先の frame、半 hop の support 外は無声。時間ずれの fitting は行わない。
FCPE は `(160*i+80)/16000` 秒、末尾の複製 mel frame も保存し、
BP は既存 `model_frames_to_time(N)` の window 補正を使った。

## 結果と不採用理由

| 指標・区分 | FCPE | BP 単音投影 |
|:---|---:|---:|
| 全体 pitch F1 | 0.4744 | 0.7608 |
| 短音 pitch F1 | 0.2039 | 0.3596 |
| 反復 pitch F1 | 0.4324 | 0.6481 |
| 持続音 pitch F1 | 0.5284 | 0.9359 |
| 欠損基音 pitch F1 | 0.8202 | 0.2234 |
| 加算ノイズ pitch F1 | 0.4114 | 0.9475 |
| 純粋ノイズ false-voiced rate | 570/600 = 95.0% | 41/600 = 6.83% |
| 無音 false-voiced rate | 0/200 | 0/200 |

BP の正解 bin が <0.3 の有声 frame のうち、FCPE が正しい pitch を得たのは
218/534 = 40.82%。ただし短音は **0/114**、欠損基音が **172/214** と救済の大半を占め、
反復は 37/74、持続音は 9/107、加算ノイズは 0/25 だった。
E1 の倍音合成では正解約 41.20 Hz に対し約 55 Hz を出す個別観察もあった。
これは合成 fixture の観察であり、実演奏の誤り率の推定ではない。

事前 gate は救済率 >=20%、全体 pitch F1 が BP 投影より高いこと、
純粋ノイズ・無音の各 category-micro false-voiced rate <=5% のすべて。
救済率と無音は通過したが、全体 F1 と純粋ノイズは不通過のため不採用。
BP 全 active-bin-set F1 は 0.5991。BP 既定 events は 90 notes / 76 references、
onset match 36、onset+offset match 14。後者の許容値と一対一 matching はプロトコルに記録した。

micro F1 は長い音の寄与が大きく、短音・反復は区分別に読む必要がある。
有声両方の median absolute cents は FCPE 24.53 / BP 0.0 だが、無声落ちを除く条件付き統計。
境界の最初の正しい frame は連続性の観察であり、同音の連続 attack の onset 検出を示さない。

## 再計算と推論の再現範囲

公開集計だけの再計算は repository root で次を実行する。モデル、音源、追加依存は不要。
誤 pitch は pitch FP と FN の双方に数え、無定義の分母は `None` とする。

```python
import hashlib, json
from pathlib import Path
base = Path('docs/experiments/fcpe-synthetic-20261025')
r = json.loads((base / 'results.json').read_text())
assert hashlib.sha256((base / 'protocol.json').read_bytes()).hexdigest() == r['protocol_sha256']
def ratio(n, d):
    return n / d if d else None
def metrics(c):
    return {'pitch_precision': ratio(c['pitch_tp'], c['pred_voiced']),
            'pitch_recall': ratio(c['pitch_tp'], c['gt_voiced']),
            'pitch_f1': ratio(2*c['pitch_tp'], c['pred_voiced']+c['gt_voiced']),
            'voicing_f1': ratio(2*c['voice_tp'], c['pred_voiced']+c['gt_voiced']),
            'false_voiced_rate': ratio(c['false_voiced'], c['unvoiced_frames'])}
for model, overall in r['overall_counts'].items():
    assert all(overall[k] == sum(v[model][k] for v in r['category_counts'].values()) for k in overall)
    print(model, metrics(overall))
    for category, models in r['category_counts'].items():
        print(category, metrics(models[model]))
rec = r['recovery']
assert all(rec[k] == sum(v[k] for v in r['recovery_by_category'].values())
           for k in ('rescued_frames', 'eligible_low_bp_frames', 'gt_voiced_frames'))
gates = {'rescue_ge20percent': ratio(rec['rescued_frames'], rec['eligible_low_bp_frames']) >= .2,
         'pitch_f1_gt_basic_pitch_raw': metrics(r['overall_counts']['fcpe'])['pitch_f1'] > metrics(r['overall_counts']['basic_pitch'])['pitch_f1'],
         'pure_noise_false_voiced_le5percent': metrics(r['category_counts']['pure_noise']['fcpe'])['false_voiced_rate'] <= .05,
         'silence_false_voiced_le5percent': metrics(r['category_counts']['silence']['fcpe'])['false_voiced_rate'] <= .05}
assert gates == r['decision_gates'] and all(gates.values()) == r['promising_frame_candidate']
print(gates, 'adopt:', all(gates.values()))
```

元の推論を再現する際の手順は以下。今回の記録作業では再実行していない。

1. 原 harness を保有する隔離領域で、`results.identities.original_harness_sha256` を照合する。
   原 harness がない環境では本記録だけから実行できる完全 runner はない。
2. FCPE source を上記 commit、重み・BP ONNX・source files を protocol の SHA-256 と照合する。
   観測環境は Python 3.11.17 / Torch 2.9.1 / NumPy 1.26.4 / librosa 0.11.0 /
   ONNX Runtime 1.23.2。補助依存の版も results に記録。取得・依存導入はこの文書の実行には不要。
3. protocol の各 clip seed と integer labels から生成し、全 WAV hash を照合する。
   位相 0 の `sum(sin(2*pi*h*f0*t)/h)` に attack/release/decay を乗じ、event RMS 0.12 にする。
   PCG64 Gaussian noise は active samples の RMS で SNR を合わせ、clip 全体へ加算し、
   `rint(x*32767)` の PCM16 とする。partial・envelope・全 event 時刻は protocol を使用する。
4. 新しい出力先でモデル別の fresh CPU subprocess を使い、intra-op 4 / inter-op 1、ORT sequential。
   原 harness では `python -m harness.run --run-dir NEW_DIR --approved-protocol-sha256 HASH`、
   完了後 `python -m harness.analyze NEW_DIR`。HASH は保存済み protocol hash。
   raw arrays を既定 event decoder の前にコピーし、native timestamps と既定 events を残す。
   初回と、`sustain_E1_2s` の warm 2 回を分離して保存する。閾値探索は含めない。

warm 2.4 秒 clip の wall は FCPE 0.0356/0.0387 秒、BP 0.0404/0.0462 秒。
model load は 0.1313/0.0321 秒、初回推論は 0.5370/0.9150 秒、process peak RSS は
733.6/221.3 MiB（FCPE/BP）。F0 のみと note/pitch-bend decoding 込みで仕事量が異なり、
速度優位の証明にはしない。imports は NumPy の先行 import を除き、active worker は起動・hash 検証を除く。
parent subprocess wall と BP raw/decoder 別計測は results に保持した。

検証は公開 JSON の hash・集計再計算・リンク確認のみ。原実験では model 不要の意味論テスト
6 件と既存 evaluation unit tests 66 件が通過し、別の既存 backend baseline 確認も
100 passed / 4 warnings / 5.09 秒だった。現在の文書確認とは別の観測記録である。
新候補探索・追加推論は行わない。

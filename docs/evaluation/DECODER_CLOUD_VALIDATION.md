# デコーダ診断と通常HTTPアプリのクラウド検証

2026-10-31 UTC。基準コミットは `a8b36e810482dd291ee5e3fda3b3317a25bdb5fe`。
クラウドの通常導線・型・連続切替修正を保持してローカル担当のパッチを加えた。
未コミットで検証し、公開・push・merge・deployは行っていない。

## 受領パッチの確認

| ファイル | bytes | SHA-256 |
| --- | ---: | --- |
| decoder-worker-core.patch | 13610 | 18fe893c3ced35db1a7c6fcbe23e0c2705a30a3e0c2d19aabe5bb6d9803c0728 |
| decoder-worker-docs.patch | 1954 | 8135f9a7ce2fbfc12e5d40e1919e49dd1f68cc18866073201d31627f8892ee95 |
| api-fixture-suggestion.patch | 644 | 6f29decab9566aff2a52a4e9d34617a761385378d46bc9082c3dc17c9c599376 |

coreの会話原文にはHTMLエスケープがあった。受領文字列を保存し、`html.unescape`で
`&lt;` / `&gt;`を戻したUTF-8・BOM無し・LF・末尾改行ありのバイト列が、指定サイズと
ハッシュに完全一致した。この一致を確認してから3件を`git apply --check`し、適用した。
READMEとTESTINGにあったクラウド変更も保持した。

## 実装と失敗の意味

- `strict-time-order-v1`は負・ゼロ音符長、不正境界、チャンク内での絶対shiftの後戻りを診断する。
  チャンクの時刻リセット、tieの前置部、ゼロshiftは区別する。
- workerは元tokens/events/notesと`decoder-integrity.json`を保存する。不正なら
  `invalid_model_output`でMIDI出力より前に失敗する。`notes_modified=false`、自動再試行無し。
- 音符の削除・整列・時刻丸め・offset補正は追加していない。拒否は異常音符の修復ではなく、
  推論精度の改善を意味しない。公式モデルのイベント列も変更していない。
- API失敗回帰ではraw保持、成功MIDIと完了成果物無し、成功譜面ID無しを確認した。
  フロント回帰ではエラー表示と自動POST再送無しを確認した。

## 再送fixtureの根拠

既存の`audio()`を1.1秒空けて2回呼ぶと、両WAVは6480 bytesで、デコード波形が一致する一方、
PEAKチャンクの時刻フィールドによりbyte offset 60だけが異なった。APIは入力のバイト列で
同一要求を判定するため、その409は正しい。製品の競合判定は変更していない。

再送テストは生成済みの同じバイト列を2回渡すfixtureへ修正した。別の回帰ではRIFF JUNK
メタデータだけを変え、波形一致・バイト不一致を確認したうえで409と再実行無しを検証する。
同じバイトなら既存IDの同じ結果を返し、異なるバイトなら元入力を上書きしない。

## 自動テストの結果

| 対象 | 最終結果 |
| --- | --- |
| backend `tests cloud_tests` | 312成功、非推奨警告5件 |
| frontend Vitest | 150成功、17ファイル |
| TypeScript `tsc --noEmit` | 成功、診断0件 |
| Vite production build | 成功 |
| 実Chromium・通常HTTPアプリ | 合成入力9項目成功 |
| 実Chromium・デコーダ失敗表示 | 合成失敗1項目成功 |

backendの全評価テストを含めて実行した。デコーダとworkerは合成データ・モデル模擬で検証した。
既存Basic Pitch統合スモークだけは1秒の合成トーンにONNXを実行する。
Python 3.11.17、Node 24.19.0、既存ロックと一致する依存を再利用した。新規インストールは無い。

```bash
cd /workspace/scratch/stem2tab-latest-ztqdgO/backend
PYTHONDONTWRITEBYTECODE=1 /workspace/Stem2Tab/backend/.venv/bin/python -m pytest tests cloud_tests -q -p no:cacheprovider
cd ../frontend
npm test -- --run
node node_modules/typescript/bin/tsc --noEmit
npm run build
```

## 実ブラウザで通した境界

通常HTTPアプリと実alphaTab/WebAudioを使用した。推論の代わりに専用ストレージへ生成MIDIの
完了ジョブを置き、音源3種も同じ合成MIDIから作成した。実曲の原音・分離音ではない。

1. 通常完了ジョブの生成MIDI導線から保存インポートし、8音と原MIDIのSHAを保持。
2. alphaTabのSVG描画とsoundfontの準備完了。
3. 実SDKで再生・一時停止・シーク・再開・停止。
4. TABと譜間隔の反復切替、再生中の時刻カーソルの移動。
5. 明示した運指・120 BPM・4/4・原点0・格子8・最大30 msで別版を作成し、原入力不変。
6. GP5を再読込し、8論理音・9音片・タイ1・休符8・3小節を保持して描画・再生・停止。
7. 合成音源の5回連続切替、1〜2秒ループ、一時停止・シーク・停止。
8. 新規の合成ファイル選択からAPIへ送信し、新しい譜面を描画。
9. 再生中の譜間隔変更で停止・0へ戻り、新入力への遷移で古いビューアを破棄。
10. 不正音符を出す実workerのモデル模擬テストで生成したrawを使用し、HTTP失敗状態を表示。
    `invalid_model_output`、成功譜面リンク無し、自動POST無し、rawのSHA不変、MIDI無しを確認。

10はモデル模擬workerの成果物を用いて終了状態を専用ストレージへ配置した試験で、
ブラウザから実MuScriptor推論を完走した試験ではない。通常9項目と失敗表示の記録に
page errorは無い。旧`file://`拒否対象にはアクセスせず、許可された新規HTTP試験だけを行った。
APIとフロントはloopback限定で起動し、終了後に8290/4290の待受が無いことを確認した。

機械可読な証拠とHTTP再実行スクリプトは
`/workspace/scratch/stem2tab-decoder-integration-evidence/`に保存する。
スクリプトは新しい専用ディレクトリで実行し、終了時に自分で起動したサーバーを停止する。

## 未検証と次の入力

クラウドには実曲、独立確認済み正解、MuScriptor重み、利用可能なCUDAが無い。
実曲精度、分離品質、offsetの改善、実奏での運指、利用者の実際の聴取は未検証。
ローカルから共有されたIDMT評価値をクラウドの再現結果には含めない。
次に必要なのは素材の外部転送ではなく、ローカル側の評価コードSHA、正解出典と確認方法、
許容値、区間別のmissing/extra/octave/onset/offset、原時刻と別量子化版の差分、実奏所見である。
既定方式、重みの非商用条件、従来GP5の制約は維持している。

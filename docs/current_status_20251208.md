# 現状まとめ（2025-12-08）

## フロントエンド
- AlphaTab は **MusicXML のみ表示**（GP系はダウンロード専用）。GP5 を渡した際の worker エラーを回避。
- ポーリングは SUCCESS で停止し、AlphaTab の再初期化を抑制。
- AlphaTabViewer の再初期化要因となっていたコールバック依存を整理（再生が途切れないように修正）。
- `Upload.tsx` の `useEffect` 未インポートによるホーム画面クラッシュを修正。

## バックエンド
- MIDI→GP5 は従来どおり。  
- MusicXML は AlphaTab 向けの安全フォーマットとして生成（単一ボイス・量子化、テンポは MIDI の tempo map 優先）。  
- MusicXML 出力時の inexpressible duration エラーを量子化で回避。
- MIME マップを拡充（GP3/4/5/GPX/XML/MXL/musicxml）。

## 直したバグ
- フロント初期化クラッシュ（`useEffect is not defined`）を解消。
- AlphaTab の過剰再初期化で再生が止まる問題を解消。
- MusicXML 生成時の forward/backup 由来エラーや inexpressible duration エラーを解消。
- GP5 を AlphaTab に渡して落ちる問題を「表示対象から除外」で回避（GP5 はダウンロード専用に切替）。

## 残っている課題
- **MIDI 品質が悪くリズムも不正確**（Basic Pitch 推論結果が劣悪）。GP5/MusicXML とも元の MIDI に依存するため譜面全体が実用に耐えない。  
- GP5 を AlphaTab で直接表示すると依然クラッシュするため非表示運用中。必要なら GP5 生成物の構造調査が要る。

## 追加で必要な作業（要検討）
- Basic Pitch の推論パラメータをベース用にチューニング  
  - 例: `maximum_frequency` を 300Hz 程度に制限、onset/offset 閾値の調整など。
- Demucs stem ではなく原音で推論する比較（分離アーチファクト影響を確認）。
- 別モデルによる音高推定の検証（精度改善のため）。
- GP5 表示を必要とする場合の AlphaTab クラッシュ要因調査（ファイル内容の簡素化・正規化など）。

## 確認用アーティファクト
- 例: `ba80e3f7-5249-46c0-a2aa-1001ff0ab5ac`  
  - `bass.wav`: Demucs 抽出ベース  
  - `bass.mid`: Basic Pitch 推論（品質が問題）  
  - `bass.gp5`: タブ譜（MIDI由来）  
  - `bass.musicxml`: AlphaTab 表示用（量子化済）


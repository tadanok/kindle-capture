# kindle-capture

Kindle for Mac の表示ページを自動でキャプチャし、PDFにまとめるmacOS用スクリプトです。既定ではOCRも実行し、検索可能PDFと読み上げ用テキストを作成します。

## 注意

- 自分が著作権を持つコンテンツ、パブリックドメインの作品、または明確な許諾を得たコンテンツにのみ使用してください。
- 作成したPDFや画像を公開・共有・配布しないでください。
- Kindleの利用規約、著作権法、所属組織のルールに従ってください。
- DRMなどのコンテンツ保護機能を回避するツールではありません。

生成されるPDF、画像、OCRモデルはGit管理の対象外です。

## 必要環境

- macOS
- [Kindle for Mac](https://www.amazon.co.jp/kindle-dbs/fd/kcp)
- Python 3
- Homebrew（OCRを使う場合）

## セットアップ

```zsh
git clone https://github.com/tadanok/kindle-capture.git
cd kindle-capture
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

OCRを使う場合は、OCRmyPDF、Tesseractの言語データ、高精度モデルを準備します。

```zsh
brew install ocrmypdf tesseract-lang
python scripts/install_ocr_models.py
```

マンガOCRではmacOSのVisionも端末内で使用します。初回だけXcode Command Line Toolsをインストールしてください。

```zsh
xcode-select --install
```

## macOSの権限

「システム設定 → プライバシーとセキュリティ」で、実行するターミナルやVS Codeに次の権限を許可します。

- アクセシビリティ
- 画面収録

設定後は実行元アプリを再起動してください。

## 実行方法

Kindleで取得したい最初のページを開き、まず3ページだけ試します。

```zsh
source .venv/bin/activate
python kindle_capture.py --max-pages 3 --keep-images
```

保存されたPNGで本文の範囲を確認できたら、ページ数制限を外して実行します。

```zsh
python kindle_capture.py
```

開始までのカウントダウン中にKindleを最前面へ表示してください。処理完了の通知が出るまではKindleを操作しないでください。

通常終了時は、Kindleの詳細画面を取り込まないよう最後の1ページを既定で除外します。本文の最終ページも必要な場合は `--keep-last-page` を指定してください。ページ数制限または手動中断時は、最後のページを残します。

既存の出力がある場合は、上書き前に `上書きしますか？ [Y/N]` と確認します。自動実行で確認を省略する場合だけ `--overwrite` を使用してください。

## マンガを取得する

```zsh
python kindle_capture.py --ocr-content-type manga
```

マンガモードは吹き出しや字幕の候補領域を切り出し、縦書きと横書きを個別にOCRします。既定の `narrative` 範囲では、タイトルと吹き出しを優先し、画面内のコードや小さなUI文字を読み上げ対象から除外します。

> **マンガOCRの精度は、通常の横書き書籍より低くなります。**
> 縦書き、装飾文字、曲線状の吹き出し、絵との重なり、低コントラストなどにより、誤認識や文章の欠落が発生します。タイトルも取得を試みますが保証はありません。完成後は `kindle_book_readaloud.txt` と元画像を確認してください。

画面内の文字を可能な限り含める場合は、次のように指定します。ただし、コードやUI文字も混入しやすくなります。

```zsh
python kindle_capture.py --ocr-content-type manga --manga-text-scope all
```

通常書籍の既定値は `document` です。マンガ向けの領域抽出やVision OCRは使用しないため、通常の横書き書籍の処理には影響しません。

## 出力ファイル

既定では次のファイルを作成します。

| ファイル | 内容 |
| --- | --- |
| `kindle_book.pdf` | キャプチャ画像をまとめたPDF |
| `kindle_book_searchable.pdf` | OCRテキスト層を持つPDF |
| `kindle_book_ocr.txt` | OCR生テキスト |
| `kindle_book_readaloud.txt` | 読み上げ向けに整形したテキスト |
| `kindle_book_ocr_quality.json` | ページ別のOCR品質と要確認候補 |

OCRの品質スコアは確認ページを選ぶための目安であり、文字の正しさを保証するものではありません。

## よく使うオプション

```zsh
# OCRを使わずPDFだけ作成
python kindle_capture.py --no-searchable

# 最後のページを残す
python kindle_capture.py --keep-last-page

# 縦書きの通常書籍
python kindle_capture.py --ocr-layout vertical --ocr-lang jpn_vert

# 複雑な段組
python kindle_capture.py --ocr-layout auto

# OCR速度を優先
python kindle_capture.py --ocr-model standard

# 表紙などを読み上げ用TXTから除外
python kindle_capture.py --readaloud-skip-pages 1-2

# AI・RAG関連の辞書と補正を追加
python kindle_capture.py --ocr-dictionary common --ocr-dictionary ai \
  --ocr-correction-profile ai-rag

# UIの自動切り取りを使わず、範囲を指定
python kindle_capture.py --no-auto-crop-ui \
  --crop-left 60 --crop-right 60 --crop-bottom 40
```

すべてのオプションは次のコマンドで確認できます。

```zsh
python kindle_capture.py --help
```

## 停止方法

- `Ctrl+C` を押す
- マウスを画面左上隅へ素早く動かす

取得済みのページがあれば、停止後も通常PDFを作成します。

## トラブルシューティング

### Kindleが見つからない、画面を取得できない

Kindleで本を開き、実行元アプリの「アクセシビリティ」と「画面収録」を確認してください。

### OCRに失敗する

```zsh
brew install ocrmypdf tesseract-lang
python scripts/install_ocr_models.py
```

### 不要なUIやページ番号が残る

`--max-pages 3 --keep-images` でPNGを確認し、必要に応じて `--header-height`、`--crop-left`、`--crop-right`、`--crop-bottom` を調整してください。

### OCRの誤認識や欠落がある

`kindle_book_ocr_quality.json` で要確認ページを探し、元画像と比較してください。マンガでは完全な自動文字起こしは難しいため、読み上げ用テキストの手直しが必要になる場合があります。

## ライセンス

コードは [MIT License](LICENSE) で公開しています。このライセンスはKindleコンテンツや生成物の利用権を付与するものではありません。

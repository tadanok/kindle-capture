# kindle-capture

Kindle for Mac で開いている本を自動でページ送りしながらスクリーンショットし、PDF にまとめる macOS 用スクリプトです。既定では OCR も実行し、本文を検索できる PDF も作成します。

## できること

- Kindle ウィンドウを検出して、表示中のページ領域をキャプチャ
- ページ送りを繰り返し、画面が変化しなくなった時点で停止
- キャプチャ画像を1つの PDF に結合
- OCR により検索可能な PDF を追加作成（日本語・英語が既定）

## 必要なもの

- macOS
- [Kindle for Mac](https://www.amazon.co.jp/kindle-dbs/fd/kcp)
- Python 3
- Homebrew（OCR を使う場合）

## 利用上の注意

- このツールは、自分が著作権を持つコンテンツ、パブリックドメインの作品、または著作権者から明確な許諾を得たコンテンツにのみ使用してください。
- キャプチャして作成したPDFや画像を、GitHubを含む公開の場所へアップロード・共有・配布しないでください。
- Kindleコンテンツの利用規約と、適用される著作権法・所属組織のルールに従ってください。
- このツールは、DRMなどのコンテンツ保護機能を回避する目的では使用しません。そのような機能の回避や無効化は行わないでください。

このリポジトリはスクリプトのみを公開対象とし、キャプチャで生成されるPDFおよび一時画像はGit管理の対象外です。

## 初回セットアップ

まだリポジトリを取得していない場合は、ターミナルで次を実行します。

```zsh
git clone https://github.com/tadanok/kindle-capture.git
cd kindle-capture
```

すでに取得済みの場合は、このリポジトリのフォルダでターミナルを開くか、そのフォルダへ移動してください。

続けて、仮想環境を作成して必要なライブラリをインストールします。

```zsh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`source .venv/bin/activate` が成功すると、プロンプトの先頭に `(.venv)` と表示されます。この状態では `python` コマンドで仮想環境内の Python を使えます。

### OCR を使うための追加セットアップ

検索可能 PDF の作成には、`ocrmypdf` コマンドと Tesseract の日本語言語データが必要です。Homebrew がある場合は、次を実行してください。

```zsh
brew install ocrmypdf tesseract-lang
```

日本語と英語の言語データを確認するには、次を実行します。

```zsh
tesseract --list-langs
```

出力に `jpn` と `eng` が含まれていれば準備完了です。

## macOS の権限設定

スクリプトが Kindle の画面を取得し、ページを送るために権限が必要です。

**システム設定 → プライバシーとセキュリティ** で、実行元のアプリ（ターミナル、iTerm、または VS Code など）に次の権限を許可してください。

- **アクセシビリティ** — Kindle を操作してページを送るため
- **画面収録** — Kindle ウィンドウをキャプチャするため

権限を変更した後は、ターミナルまたは VS Code を再起動してください。

## 実行方法

1. Kindle for Mac を起動し、キャプチャする本を開きます。
2. プロジェクトのフォルダで仮想環境を有効にします。
3. 次のコマンドを実行します。

```zsh
source .venv/bin/activate
python kindle_capture.py
```

開始まで5秒のカウントダウンがあります。その間に Kindle を最前面に表示し、取得したい最初のページを開いてください。

> 注: 画面遷移が完了したあとも内部処理が継続する場合があります。「キャプチャ完了」の通知が出るまで、Kindle 画面や他の操作を控えてください。

### きれいにキャプチャするためのヒント

このツールは、見開きではなく縦長の1ページをキャプチャすることを意図しています。macOSのSplit View（フルスクリーン状態での左右分割）を使い、Kindleを左半分または右半分に表示して、もう半分にターミナル（VS Code内の統合ターミナルでも可）を表示した状態で実行してください。通常のタイル表示ではなく、このフルスクリーン状態の左右分割にすることで、きれいにキャプチャできます。

特に、書籍画面の上端に細い線が写り込む環境では、この配置にするとタイトルバーと本文の境界が安定し、線の写り込みを防げます。表示環境によって差があるため、最初は `--max-pages 3` などで数ページだけ試すことをおすすめします。

処理が終わると、以下のファイルが作成されます。

- `kindle_book.pdf` — キャプチャした画像をまとめた PDF
- `kindle_book_searchable.pdf` — OCR により本文を検索できる PDF

## よく使うオプション

```zsh
# 出力ファイル名を指定
python kindle_capture.py --output my_book.pdf

# 最大50ページだけキャプチャ（動作確認に便利）
python kindle_capture.py --max-pages 50

# ページ変化の待機時間を2秒にする
python kindle_capture.py --delay 2

# Kindle の右側をクリックしてページを送る
python kindle_capture.py --page-turn-method click

# キー入力（下矢印）でページを送る。既定値
python kindle_capture.py --page-turn-method key

# OCR済みPDFの出力先と言語を指定
python kindle_capture.py --searchable-output result_ocr.pdf --ocr-lang jpn+eng

# 中間のPNG画像を残す
python kindle_capture.py --keep-images

# 自動検出が合わない場合だけ、ヘッダーを34pxとして指定
python kindle_capture.py --header-height 34
```

| オプション | 内容 | 既定値 |
| --- | --- | --- |
| `-o`, `--output` | 通常PDFの出力先 | `kindle_book.pdf` |
| `-d`, `--delay` | ページ変化を待つ最大秒数 | `5.0` |
| `--start-delay` | 開始前のカウントダウン秒数 | `5` |
| `--max-pages` | 最大キャプチャページ数。`0` は無制限 | `0` |
| `--keep-images` | 中間PNGを削除せず残す | 指定時のみ有効 |
| `--header-height` | 切り取るヘッダー高さ（px）。通常は自動検出 | 自動検出 |
| `--searchable-output` | OCR済みPDFの出力先 | `<出力名>_searchable.pdf` |
| `--ocr-lang` | OCRの言語 | `jpn+eng` |
| `--page-turn-method` | ページ送り方法: `key` または `click` | `key` |
| `--page-turn-retries` | ページ送りの再試行回数 | `2` |

## 停止するには

- `Ctrl+C` を押す
- マウスを画面の左上隅に素早く動かす（PyAutoGUI のフェイルセーフ）

途中で停止しても、取得済みのページがあれば PDF を作成します。

## トラブルシューティング

### `zsh: command not found: python`

仮想環境が有効ではありません。次を実行してから再度試してください。

```zsh
source .venv/bin/activate
python kindle_capture.py
```

`.venv/bin/activate` が見つからない場合は、まだ仮想環境を作成していません。「初回セットアップ」を実行してください。

### Kindle ウィンドウが見つからない

Kindle for Mac を起動して本を開き、ウィンドウを画面上に表示してから実行してください。

### 画面を取得できない、ページが送れない

実行元アプリに「画面収録」と「アクセシビリティ」の両方が許可されているか確認してください。設定後はアプリを再起動します。

### 検索可能 PDF の作成に失敗する

次で OCR に必要なツールと言語データをインストールし、再度実行してください。

```zsh
brew install ocrmypdf tesseract-lang
```

## ライセンス

このリポジトリ内のコードは [MIT License](LICENSE) のもとで公開しています。ライセンスの対象はコードのみであり、Kindleコンテンツや、このツールで生成したPDF・画像の利用権を付与するものではありません。

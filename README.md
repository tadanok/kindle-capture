# kindle-capture

Kindle for Mac で開いている本を自動でページ送りしながらスクリーンショットし、PDF にまとめる macOS 用スクリプトです。既定では OCR も実行し、検索可能 PDF、OCR 生テキスト、読み上げ向けに整形したテキストも作成します。

本ツールは、検索可能 PDF を音声読み上げで利用しやすくすることを優先しています。OCR結果を画面上の全要素まで忠実に文字化することよりも、本文を自然な順序で読み上げられることを重視し、不要な空白、ページ番号、低信頼のノイズ、図表内の文字などを既定でテキスト層から除外します。除外対象も元のページ画像には残るため、画面上では確認できます。

## できること

- Kindle ウィンドウを検出して、表示中のページ領域をキャプチャ
- ページ送りを繰り返し、画面が変化しなくなった時点で停止
- キャプチャ画像を再圧縮せず、300 DPI の PDF に結合
- OCR により検索可能な PDF を追加作成（日本語が既定）
- OCR が挿入した日本語間の空白やページ番号などを整え、読み上げ用 TXT を作成
- 本文の上下左右の切り取りと、横書き・縦書きの OCR 設定を指定可能

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

検索可能 PDF の作成には、OCRmyPDF 17.8.1 以降と Tesseract の日本語言語データが必要です。Homebrew がある場合は、次を実行してください。

```zsh
brew install ocrmypdf tesseract-lang
python scripts/install_ocr_models.py
```

OCRmyPDF のバージョンは `ocrmypdf --version` で確認できます。

`scripts/install_ocr_models.py` は、既定で使用する公式の高精度モデル
[`tessdata_best`](https://github.com/tesseract-ocr/tessdata_best) から
`jpn`、`jpn_vert`、`eng` を `ocr_models/tessdata_best/` に取得します。
モデルは約 43 MB で、Git の管理対象には含まれません。

標準モデルの日本語と英語の言語データを確認するには、次を実行します。

```zsh
tesseract --list-langs
```

通常の日本語書籍では、出力に `jpn` が含まれていれば準備完了です。英語を多く含む書籍では `eng`、縦書きを明示する場合は `jpn_vert` も確認してください。

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

ページ送り後に画面が変化しなくなった場合、最後に保存したページは Kindle の詳細画面である可能性が高いため、既定で除外します。最後のページも書籍本文として残したい場合は `--keep-last-page` を指定してください。`--max-pages` で停止した場合や手動で中断した場合は、本文を誤って落とさないよう最後のページを残します。

### きれいにキャプチャするためのヒント

このツールは、見開きではなく縦長の1ページをキャプチャすることを意図しています。macOSのSplit View（フルスクリーン状態での左右分割）を使い、Kindleを左半分または右半分に表示して、もう半分にターミナル（VS Code内の統合ターミナルでも可）を表示した状態で実行してください。通常のタイル表示ではなく、このフルスクリーン状態の左右分割にすることで、きれいにキャプチャできます。

特に、書籍画面の上端に細い線が写り込む環境では、この配置にするとタイトルバーと本文の境界が安定し、線の写り込みを防げます。OCR の誤認識を減らすため、可能であれば白背景・黒文字にし、文字を小さくしすぎないでください。

表示環境によって差があるため、最初は `--max-pages 3 --keep-images` で試してください。左右端に連続する暗い Kindle UI 帯は既定で自動検出して除外します。端から続く暗色領域と、その内側の非暗色領域を両方確認できた場合だけ除外するため、暗い表紙全体は切り取りません。PNG にタイトルバー、進捗表示、ページ番号などが残る場合は `--header-height`、`--crop-left`、`--crop-right`、`--crop-bottom` を調整してください。

処理が終わると、以下のファイルが作成されます。

- `kindle_book.pdf` — キャプチャした画像をまとめた PDF
- `kindle_book_searchable.pdf` — 日本語の不要な空白と単独ページ番号を抑えた検索・読み上げ対応 PDF
- `kindle_book_ocr.txt` — OCR の生テキスト
- `kindle_book_readaloud.txt` — 日本語の不要な空白などを除去した読み上げ用テキスト
- `kindle_book_ocr_quality.json` — ページ別の信頼度、再OCR、ノイズ除去数、頻出誤認識の補正内容、要確認候補、完成PDFの再検査結果、処理時間

出力先は処理開始前に絶対パスへ正規化し、PDFは `.pdf`、テキストは `.txt`、品質レポートは `.json` であることを確認します。同じファイルが複数の出力先に指定された場合は、キャプチャ開始前にエラーで停止します。

既存の出力ファイルがある場合は対象を一覧表示し、`上書きしますか？ [Y/N]` と確認します。`Y` または `YES` の場合だけ処理を続け、`N`、`NO`、Enter、入力終了の場合は上書きせず中止します。入力は大文字・小文字のどちらでも受け付けます。自動実行などで確認を省略する場合だけ `--overwrite` を指定してください。生成物は一時ファイルへ書き込み、完成後に置換します。

検索可能 PDF の不可視テキスト層は、既定で各日本語行を空白のない1つの文字列として埋め込みます。英語の単語間空白は維持され、単独のページ番号はテキスト層から除外されます。

複数ページで繰り返すヘッダーやフッターも除去したい場合や、読み上げアプリが TXT に対応している場合は、さらに後処理された `kindle_book_readaloud.txt` を使用してください。

既定では公式の高精度モデルを使用します。また、文字数、OCR信頼度、記号ノイズの割合から低品質と判断したページだけを、別のページ分割方式で1回再OCRします。表紙や図表のように文字が少ないページでは疎な文字配置向け、本文で信頼度が低いページでは自動レイアウト解析を使用し、信頼度スコアが改善した場合だけ再OCR結果を採用します。全ページを複数方式で総当たりする処理ではありません。

OCR開始時には `ocr_dictionaries/common.txt` をTesseractの専門用語辞書として読み込みます。辞書はTesseract本体へ登録せず、OCR実行中だけ `--user-words` で渡します。AI・RAG関連書籍では `--ocr-dictionary ai` を追加してください。共通辞書とAI辞書を併用する場合は `--ocr-dictionary common --ocr-dictionary ai` と指定します。独自辞書は `--ocr-user-words FILE` で追加でき、1行に1語を記述します。

短く孤立した低信頼の記号ノイズは、検索可能 PDF のテキスト層と読み上げ用 TXT から除外します。さらに、行頭で孤立し、同じX座標に並ぶ黒丸やチェック欄は、黒画素率、形状、本文との間隔からリスト記号として判定し、不可視テキスト層だけから除外します。番号付きリストとページ画像は変更しません。除外内容は品質レポートの `filtered_list_markers` に記録されます。確認用の `kindle_book_ocr.txt` には、採用したOCR方式の未加工テキストを残します。

頻出誤認識の補正は、他の書籍へ固有補正を誤適用しないようプロファイルで分離しています。既定の `common` は一般的で保守的な補正だけを行います。`LIM` → `LLM`、`Al` → `AI` などは `--ocr-correction-profile ai-rag`、今回確認した書籍固有の章見出しや文脈補正まで含める場合は `--ocr-correction-profile rag-accuracy-book` を指定してください。`rag-accuracy-book` は `common` と `ai-rag` も自動的に含みます。

補正は検索可能 PDF のテキスト層へ埋め込む直前に適用し、完全一致または限定した前後関係だけを対象として各行の文字座標を維持します。使用したプロファイル、補正内容、件数は `kindle_book_ocr_quality.json` に記録されます。未加工の認識結果を確認できるよう、`kindle_book_ocr.txt` は補正しません。

自動補正する根拠が不足している不自然な文字列は、その行だけを拡大して1行専用モードで再OCRします。疑わしさが解消し、信頼度が十分に改善した場合だけ採用します。それでも確定できない文字列は変更せず、`review_candidates` としてページ番号、文字列、信頼度、検出理由を品質レポートに記録します。完成したPDFからもテキストを再抽出し、既知の補正漏れや見出し結合は `post_validation_candidates` に記録します。

品質レポートの候補件数は自動ヒューリスティックで検出できた件数であり、0件でも原画像との完全一致を保証しません。`quality_verification` には検査方式、正解データとの比較有無、自動検査の通過状態を記録します。重要な書籍では、代表ページを原画像と目視比較してください。

図表内の文字は読み順が崩れたり、線やノードを文字として誤認識したりしやすいため、既定では検索可能 PDF のテキスト層と読み上げ用 TXT の両方から除外します。色付きグラフに加え、白黒の図解、画面キャプチャー、低信頼で不規則に配置された短いラベルも周囲の本文と区別します。図表画像そのものは PDF に残り、OCR 生テキストにも未加工の認識結果を残します。図表内の文字も検索・読み上げ対象にする場合だけ `--include-figure-text` を指定してください。

### 読み上げ用テキストを別に作成する理由

検索可能 PDF のテキスト層は、検索や文字選択の位置を画像と一致させるため、ページごとの文字座標を保持して作成されます。そのため、OCR中の1ページだけでは、ページ上端や下端の文字が本文なのか、複数ページで繰り返すヘッダーやフッターなのかを安全に判断できません。

`kindle_book_readaloud.txt` は、全ページのOCR結果を結合した後に文書全体を比較して作成します。これにより、複数ページで繰り返す短いヘッダーやフッター、単独のページ番号、日本語文字間の不要な空白を除去し、表示上の折り返しも読み上げに適した文章へまとめられます。

表紙などを明示的に読み上げ対象から外す場合は、`--readaloud-skip-pages 1-2` のように指定します。この指定は読み上げ用 TXT だけに適用され、通常 PDF、検索可能 PDF、OCR 生テキストには影響しません。

このため、ページ画像との位置対応を保ちながら検索・選択・読み上げを行う場合は `kindle_book_searchable.pdf`、位置情報を必要とせず、より自然な読み上げを優先する場合は `kindle_book_readaloud.txt` を使用してください。

## よく使うオプション

```zsh
# 出力ファイル名を指定
python kindle_capture.py --output my_book.pdf

# 自動実行時に既存出力のY/N確認を省略して置き換える
python kindle_capture.py --overwrite

# 最大50ページだけキャプチャ（動作確認に便利）
python kindle_capture.py --max-pages 50

# ページ変化の待機時間を2秒にする
python kindle_capture.py --delay 2

# Kindle の右側をクリックしてページを送る
python kindle_capture.py --page-turn-method click

# キー入力（下矢印）でページを送る。既定値
python kindle_capture.py --page-turn-method key

# 日本語だけに限定して OCR
python kindle_capture.py --searchable-output result_ocr.pdf --ocr-lang jpn

# AI・RAG関連の専門用語辞書を共通辞書へ追加
python kindle_capture.py --ocr-dictionary common --ocr-dictionary ai

# AI・RAG向けの頻出誤認識補正を有効化
python kindle_capture.py --ocr-correction-profile ai-rag

# 今回確認した「RAG 精度改善」固有の補正も有効化
python kindle_capture.py --ocr-correction-profile rag-accuracy-book

# 独自の専門用語辞書を追加（1行1語）
python kindle_capture.py --ocr-user-words my_terms.txt

# Tesseract専門用語辞書を使用しない
python kindle_capture.py --ocr-dictionary none

# 表紙の1〜2ページを読み上げ用 TXT から除外
python kindle_capture.py --readaloud-skip-pages 1-2

# 処理速度を優先して標準モデルを使用
python kindle_capture.py --ocr-model standard

# 低品質ページの選択的な再 OCR を無効化
python kindle_capture.py --no-ocr-adaptive

# 低信頼ノイズの除去を無効化
python kindle_capture.py --no-filter-low-confidence

# 確認済みの頻出 OCR 誤認識補正を無効化
python kindle_capture.py --no-correct-common-ocr-errors

# 図表内の文字も検索可能 PDF と読み上げ用 TXT に含める
python kindle_capture.py --include-figure-text

# 黒丸やチェック欄も検索可能 PDF のテキスト層に含める
python kindle_capture.py --include-list-markers

# 縦書きとして OCR
python kindle_capture.py --ocr-layout vertical --ocr-lang jpn_vert

# 複雑な段組を自動解析して OCR
python kindle_capture.py --ocr-layout auto

# OCRmyPDF 標準のテキスト層へ戻す
python kindle_capture.py --pdf-text-layer standard

# 読み上げ用 TXT の出力先を指定
python kindle_capture.py --readaloud-output result_readaloud.txt

# 中間のPNG画像を残す
python kindle_capture.py --keep-images

# 最後のページも書籍本文として残す
python kindle_capture.py --keep-last-page

# 自動検出が合わない場合だけ、ヘッダーを34pxとして指定
python kindle_capture.py --header-height 34

# 左右と下端の UI を追加で除外
python kindle_capture.py --crop-left 60 --crop-right 60 --crop-bottom 40

# 左右端 UI の自動検出が合わない場合
python kindle_capture.py --no-auto-crop-ui --crop-left 60

# OCR を行わず、通常 PDF だけを作成
python kindle_capture.py --no-searchable
```

| オプション | 内容 | 既定値 |
| --- | --- | --- |
| `-o`, `--output` | 通常PDFの出力先 | `kindle_book.pdf` |
| `--overwrite` | 既存出力のY/N確認を省略して置換 | 指定時のみ有効 |
| `-d`, `--delay` | ページ変化を待つ最大秒数 | `5.0` |
| `--start-delay` | 開始前のカウントダウン秒数 | `5` |
| `--max-pages` | 最大キャプチャページ数。`0` は無制限 | `0` |
| `--keep-images` | 中間PNGを削除せず残す | 指定時のみ有効 |
| `--exclude-last-page` | 通常終了時に最後のページを除外 | 有効 |
| `--keep-last-page` | 通常終了時も最後のページを残す | 指定時のみ有効 |
| `--header-height` | 切り取るヘッダー高さ（px）。通常は自動検出 | 自動検出 |
| `--crop-left` | 左端から切り取る最小幅（px） | `50` |
| `--no-auto-crop-ui` | 左右端の暗い Kindle UI 帯を自動除外しない | 指定時のみ有効 |
| `--crop-right` | 右端から切り取る幅（px） | `50` |
| `--crop-bottom` | 下端から切り取る幅（px） | `0` |
| `--pdf-dpi` | 通常 PDF に記録する解像度 | `300` |
| `--no-searchable` | OCR とテキスト出力を省略 | 指定時のみ有効 |
| `--searchable-output` | OCR済みPDFの出力先 | `<出力名>_searchable.pdf` |
| `--ocr-text-output` | OCR 生テキストの出力先 | `<出力名>_ocr.txt` |
| `--readaloud-output` | 読み上げ用テキストの出力先 | `<出力名>_readaloud.txt` |
| `--readaloud-skip-pages` | 読み上げ用TXTから除外するページ（例: `1-2,5`） | なし |
| `--ocr-lang` | OCRの言語 | `jpn+eng` |
| `--ocr-model` | OCRモデル: `best`（高精度）または `standard`（高速） | `best` |
| `--tessdata-best-dir` | 高精度モデルの保存先 | `ocr_models/tessdata_best` |
| `--ocr-layout` | 本文レイアウト: `auto`、`horizontal`、`vertical` | `horizontal` |
| `--ocr-oversample` | OCR 前に補間する最低解像度 | `300` |
| `--ocr-dictionary` | Tesseract専門用語辞書。`common`、`ai`、`none`。複数指定可 | `common` |
| `--ocr-user-words` | 独自のTesseract専門用語ファイル。複数指定可 | なし |
| `--no-ocr-adaptive` | 低品質ページの選択的な再OCRを無効化 | 指定時のみ有効 |
| `--no-filter-low-confidence` | PDFと読み上げ用TXTの低信頼ノイズ除去を無効化 | 指定時のみ有効 |
| `--no-correct-common-ocr-errors` | PDFテキスト層へ埋め込む前の確認済み頻出誤認識補正を無効化 | 指定時のみ有効 |
| `--ocr-correction-profile` | OCR補正範囲。`common`、`ai-rag`、`rag-accuracy-book`、`none`。複数指定可 | `common` |
| `--include-figure-text` | 図表内の文字をPDFテキスト層と読み上げ用TXTに含める | 指定時のみ有効 |
| `--include-list-markers` | 黒丸やチェック欄をPDFテキスト層と読み上げ用TXTに含める | 指定時のみ有効 |
| `--ocr-quality-report` | OCR品質レポートの出力先 | `<出力名>_ocr_quality.json` |
| `--pdf-text-layer` | PDFテキスト層: `readaloud` または `standard` | `readaloud` |
| `--page-turn-method` | ページ送り方法: `key` または `click` | `key` |
| `--page-turn-retries` | ページ送りの再試行回数 | `2` |

## 停止するには

- `Ctrl+C` を押す
- マウスを画面の左上隅に素早く動かす（PyAutoGUI のフェイルセーフ）

`Ctrl+C` とマウスのフェイルセーフのどちらで停止しても、取得済みのページがあれば通常 PDF を作成します。

通常 PDF の作成後に OCR または読み上げ用 TXT の生成だけが失敗した場合は、「一部失敗」と表示して終了コード `1` を返します。通常 PDF は保存され、成功していない出力を「完了」とは通知しません。

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
python scripts/install_ocr_models.py
```

古い OCRmyPDF がインストールされている場合は、`brew upgrade ocrmypdf` で更新してください。

### 読み上げ時に不要な文字やページ番号が残る

まず検索可能 PDF からコピーした文字列、`kindle_book_ocr.txt`、`kindle_book_readaloud.txt` を比較してください。

既定の検索可能 PDF では、日本語各行の不要な空白と単独ページ番号を不可視テキスト層から除去します。読み上げ用テキストでは、さらに複数ページで繰り返す短いヘッダーやフッターも除去します。

PDFビューアとの相性で検索や選択に問題が出る場合は、`--pdf-text-layer standard` でOCRmyPDF標準のテキスト層へ戻せます。

`standard` テキスト層では、カスタムOCRプラグインを使用しないため、選択的な再OCR、低信頼ノイズ除去、頻出誤認識補正、図表内テキスト除外、リスト記号除外は適用されません。

生テキストの時点で不要な文字が認識されている場合は、本文以外の UI が画像に残っています。`--max-pages 3 --keep-images` で試し、保存された PNG を確認してください。左右端の暗い帯は自動除外されますが、検出が不足する場合は `--crop-left` または `--crop-right` を増やします。本文まで切れる場合は `--no-auto-crop-ui` と適切な切り取り幅を併用してください。

既定値は、今回確認したような横書き・単一本文領域・英単語混在の書籍に合わせて `--ocr-layout horizontal --ocr-lang jpn+eng` です。複雑な段組では `--ocr-layout auto`、縦書きでは `--ocr-layout vertical --ocr-lang jpn_vert` を試してください。

処理時間を短縮したい場合は `--ocr-model standard` を指定します。今回の3ページの確認では、選択的再OCRを含めて高精度モデルが約7.0秒、標準モデルが約3.8秒でした。実際の時間はページ数、文字量、再OCR対象ページ数、CPU性能によって変わります。`kindle_book_ocr_quality.json` の `elapsed_seconds`、`retried_pages`、`filtered_figure_lines` で実測値と図表から除外した行数を確認できます。

## ライセンス

このリポジトリ内のコードは [MIT License](LICENSE) のもとで公開しています。ライセンスの対象はコードのみであり、Kindleコンテンツや、このツールで生成したPDF・画像の利用権を付与するものではありません。

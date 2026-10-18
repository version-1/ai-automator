# DEV Community

公式 API の記事エンドポイントで候補を取得する。タグや著者が対象ページで指定されない
場合は `https://dev.to/api/articles/latest?per_page=20` を使う。タグが分かる場合は
`tag=<tag>` を指定する。個別記事 API の `body_markdown` または記事ページの本文を保存する。

`published_at` は候補の日付の根拠として保存し、`edited_at` と混同しない。

公式仕様: <https://developers.forem.com/api/v0>

# GitHub

公開一覧の `https://github.com/trending?since=daily` と
`https://github.com/trending?since=weekly` を取得する。HTML からリポジトリ名と
`stars this week` または `stars today` を取得できた場合だけ、トレンド順位・増分として
保存する。取得できない場合は `fetch_error` を残し、検索結果や総スター数から補完しない。

候補リポジトリは README を `https://raw.githubusercontent.com/<owner>/<repo>/HEAD/README.md`
から取得する。README が取れない場合はリポジトリページの内容を保存し、未確認を明示する。

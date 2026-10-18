# Hacker News

候補 ID は公式 Firebase API の `topstories`、`newstories`、`showstories` から取得し、
各 ID の `item/<id>.json` を取得する。`deleted`、`dead`、`job`、`comment` は除外する。
`time` は Hacker News への投稿日時なので、外部記事の公開日とは別の `shared_at` に保存する。

外部 URL がある候補では、リンク先本文も取得する。URL がない場合は item の `text` を
本文として保存する。score と descendants はサイト内指標として保存する。

公式仕様: <https://github.com/HackerNews/API>

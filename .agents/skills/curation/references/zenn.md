# Zenn

収集入口は公式 RSS を使う。共通フィードは `https://zenn.dev/feed`、ユーザーは
`https://zenn.dev/<user>/feed`、トピックは
`https://zenn.dev/topics/<topic>/feed` である。対象ページにユーザーまたはトピックが
含まれる場合だけ、その専用フィードを追加する。

本文確認は各候補の `link` を開き、本文取得に成功した候補だけを日次サマリの採用候補にする。
RSS は候補発見の証跡であり、トレンドの根拠にはしない。

公式仕様: <https://zenn.dev/zenn/articles/zenn-feed-rss>

# Medium

対象ページに Profile、Publication、Topic の RSS URL が明示されている場合だけ取得する。
指定がない場合はスクリプトで `fallback_required` を記録し、Web 検索またはブラウザーによる
候補を fallback JSON で取り込む。RSS URL を推測してはいけない。

候補の記事ページを取得して本文・公開日を保存する。有料部分を読めない場合は取得できた
範囲とその制約を candidate のエラーまたはメモとして残す。

公式案内: <https://help.medium.com/hc/en-us/articles/214874118-Using-RSS-feeds-of-profiles-publications-and-topics>
